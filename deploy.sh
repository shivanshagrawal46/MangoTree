#!/usr/bin/env bash
# =============================================================================
# MangoTree — one-command deploy on a DigitalOcean droplet shared with other apps.
#
#   cd <your folder> && git pull origin main && bash deploy.sh
#
# Everything else is handled here, idempotently, every run:
#   * installs missing prerequisites with apt (python3-venv, curl, git; Node 20
#     via NodeSource if node is absent or too old; nginx + certbot only if you
#     give a domain). Nothing already installed is touched or upgraded.
#   * Python virtualenv INSIDE this folder (.venv) — the system Python and every
#     other project's environment are left alone.
#   * picks two free localhost ports the first time and remembers them in
#     .deploy.env, so no other app's port is ever taken.
#   * .env: RAW_STORE -> ./raw_store, or OBJECT_STORE=spaces when DO_SPACES_* are set.
#   * builds the frontend, installs/updates two systemd services with unique
#     names (mangotree-api, mangotree-web), starts them, health-checks them.
#   * with MT_DOMAIN: writes ONE new nginx site for that domain, tests the
#     config before reloading, and runs certbot for HTTPS. Other sites untouched.
#
# First time, or to change the domain:
#   MT_DOMAIN=mangotree.yourdomain.com MT_EMAIL=you@yourdomain.com bash deploy.sh
# Later runs need no variables; they read .deploy.env.
#
# What must arrive separately (they are not in git): .env, client_secret.json,
# gmail_token.json, .secrets/graph_token_cache.json and the originals folder.
# scripts/ship.ps1 on the Windows machine sends all of them and then runs this.
# =============================================================================
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT"
STATE=".deploy.env"
[[ -f "$STATE" ]] && source "$STATE"

API_PORT="${MT_API_PORT:-${API_PORT:-}}"
WEB_PORT="${MT_WEB_PORT:-${WEB_PORT:-}}"
DOMAIN="${MT_DOMAIN:-${DOMAIN:-}}"
EMAIL="${MT_EMAIL:-${EMAIL:-}}"
#: MT_EXPOSE=ip — no domain yet: serve the web app on the droplet's IP at
#: WEB_PORT over plain HTTP (open that port in the DigitalOcean firewall).
#: Default (empty) keeps both services on localhost, reachable via SSH tunnel.
EXPOSE="${MT_EXPOSE:-${EXPOSE:-}}"
RUN_USER="${MT_USER:-${RUN_USER:-$(id -un)}}"
SERVICE_API="mangotree-api"
SERVICE_WEB="mangotree-web"

c_ok()   { printf "\033[32m  ✓ %s\033[0m\n" "$*"; }
c_warn() { printf "\033[33m  ! %s\033[0m\n" "$*"; }
c_err()  { printf "\033[31m  ✗ %s\033[0m\n" "$*" >&2; }
h()      { printf "\n\033[1m== %s ==\033[0m\n" "$*"; }
die()    { c_err "$*"; exit 1; }

SUDO=""
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || die "run as root or install sudo"
  SUDO="sudo"
fi
APT_UPDATED=0
apt_install() {  # apt_install pkg...  (only what is missing)
  local need=()
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || need+=("$p"); done
  [[ ${#need[@]} -eq 0 ]] && return 0
  if [[ $APT_UPDATED -eq 0 ]]; then $SUDO apt-get update -qq; APT_UPDATED=1; fi
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq "${need[@]}"
  c_ok "installed: ${need[*]}"
}
port_in_use() { ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$"; }
unit_active()  { systemctl is-active --quiet "$1" 2>/dev/null; }
free_port() {  # free_port start  -> first free port from start
  local p="$1"; while port_in_use "$p"; do p=$((p+1)); done; echo "$p"
}

# ------------------------------------------------------------- 1. prerequisites
h "1/7 Prerequisites (installed only if missing)"
[[ -f requirements.txt && -d mangotree && -d web ]] || die "run this inside the MangoTree folder"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
apt_install curl git ca-certificates python3 python3-venv python3-pip
PY="$(command -v python3)"
minor="$("$PY" -c 'import sys; print(sys.version_info.minor)')"
[[ "$minor" -ge 9 ]] || die "python3.9+ required, found $($PY --version)"
c_ok "python: $($PY --version 2>&1)"

need_node=1
if command -v node >/dev/null 2>&1 && [[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 18 ]]; then need_node=0; fi
if [[ $need_node -eq 1 ]]; then
  c_warn "node >= 18 not found — installing Node 20 from NodeSource (system-wide; existing node projects keep their own via nvm if they use it)"
  curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash - >/dev/null
  apt_install nodejs
fi
c_ok "node: $(node --version), npm: $(npm --version)"

# --------------------------------------------------------------- 2. secrets
h "2/7 Secrets and configuration"
if [[ ! -f .env ]]; then
  c_err ".env is missing. From the Windows machine run:  powershell -File scripts\\ship.ps1 -Server $RUN_USER@<ip> -Dest $PROJECT"
  die "or copy .env, client_secret.json, gmail_token.json and .secrets/ here, then re-run"
fi
set_env() { if grep -qE "^$1=" .env; then sed -i.bak -E "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; else printf "\n%s=%s\n" "$1" "$2" >> .env; fi; }
mkdir -p raw_store logs .secrets
chmod 600 .env
for f in client_secret.json gmail_token.json .secrets/graph_token_cache.json; do
  if [[ -f "$f" ]]; then chmod 600 "$f"; c_ok "$f"; else c_warn "$f missing — that mailbox's intake will fail until it is copied"; fi
done

# Originals: local folder, or DigitalOcean Spaces if the keys are present.
if grep -qE '^DO_SPACES_KEY=.+' .env && grep -qE '^DO_SPACES_BUCKET=.+' .env; then
  set_env OBJECT_STORE spaces
  c_ok "originals: DigitalOcean Spaces ($(grep -E '^DO_SPACES_BUCKET=' .env | cut -d= -f2-))"
else
  set_env OBJECT_STORE local
  current_raw="$(grep -E '^RAW_STORE=' .env | cut -d= -f2- || true)"
  if [[ -z "$current_raw" || "$current_raw" =~ ^[A-Za-z]: ]]; then set_env RAW_STORE "$PROJECT/raw_store"; fi
  n_orig="$(find raw_store -type f 2>/dev/null | wc -l)"
  if [[ "$n_orig" -eq 0 ]]; then c_warn "raw_store is empty — documents will list but not open until scripts/ship.ps1 has sent them"
  else c_ok "originals: $n_orig files in raw_store ($(du -sh raw_store | cut -f1))"; fi
fi

# ------------------------------------------------------------------ 3. ports
h "3/7 Ports"
if [[ -z "$API_PORT" ]]; then API_PORT="$(free_port 8017)"; fi
if [[ -z "$WEB_PORT" ]]; then WEB_PORT="$(free_port 3017)"; [[ "$WEB_PORT" == "$API_PORT" ]] && WEB_PORT="$(free_port $((WEB_PORT+1)))"; fi
for pair in "$API_PORT:$SERVICE_API" "$WEB_PORT:$SERVICE_WEB"; do
  p="${pair%%:*}"; svc="${pair##*:}"
  if port_in_use "$p" && ! unit_active "$svc"; then die "port $p is used by another app — set MT_API_PORT/MT_WEB_PORT and re-run"; fi
done
set_env API_ORIGIN "http://127.0.0.1:$API_PORT"
cat > "$STATE" <<EOF
API_PORT=$API_PORT
WEB_PORT=$WEB_PORT
DOMAIN=$DOMAIN
EMAIL=$EMAIL
EXPOSE=$EXPOSE
RUN_USER=$RUN_USER
EOF
WEB_BIND="127.0.0.1"; [[ "$EXPOSE" == "ip" && -z "$DOMAIN" ]] && WEB_BIND="0.0.0.0"
c_ok "API 127.0.0.1:$API_PORT · web 127.0.0.1:$WEB_PORT (remembered in $STATE)"

# ------------------------------------------------------------- 4. python env
h "4/7 Python environment (.venv)"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip wheel
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -c "import fastapi, uvicorn, anthropic, voyageai, pymongo, fitz, openai" && c_ok "packages installed, imports OK"
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
from mangotree.storage.mongo import get_mongo
m = get_mongo(); m.ping()
print(f"\033[32m  ✓ Mongo Atlas reachable: {m.artifacts.estimated_document_count():,} artifacts\033[0m")
PY

# ---------------------------------------------------------------- 5. frontend
h "5/7 Frontend build"
pushd web >/dev/null
if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund --loglevel=error; else npm install --no-audit --no-fund --loglevel=error; fi
API_ORIGIN="http://127.0.0.1:$API_PORT" npm run build --loglevel=error
popd >/dev/null
c_ok "next build complete"

# ----------------------------------------------------------------- 6. services
h "6/7 Services"
mkdir -p deploy
NODE_BIN="$(command -v node)"; NPM_BIN="$(command -v npm)"
cat > "deploy/$SERVICE_API.service" <<EOF
[Unit]
Description=MangoTree API (FastAPI + scheduler: mail intake, ledger, Wes agenda, briefing)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT
EnvironmentFile=$PROJECT/.env
Environment=PYTHONUNBUFFERED=1
# One worker: the scheduler and in-flight answers live in this process.
ExecStart=$PROJECT/.venv/bin/python -m uvicorn mangotree.api.app:app --host 127.0.0.1 --port $API_PORT --workers 1 --timeout-keep-alive 75
Restart=always
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGINT
StandardOutput=append:$PROJECT/logs/api.log
StandardError=append:$PROJECT/logs/api.log
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
cat > "deploy/$SERVICE_WEB.service" <<EOF
[Unit]
Description=MangoTree web (Next.js)
After=network-online.target $SERVICE_API.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT/web
Environment=NODE_ENV=production
Environment=API_ORIGIN=http://127.0.0.1:$API_PORT
Environment=PATH=$(dirname "$NODE_BIN"):/usr/local/bin:/usr/bin:/bin
ExecStart=$NPM_BIN run start -- --port $WEB_PORT --hostname $WEB_BIND
Restart=always
RestartSec=5
StandardOutput=append:$PROJECT/logs/web.log
StandardError=append:$PROJECT/logs/web.log

[Install]
WantedBy=multi-user.target
EOF
$SUDO install -m 644 "deploy/$SERVICE_API.service" "/etc/systemd/system/$SERVICE_API.service"
$SUDO install -m 644 "deploy/$SERVICE_WEB.service" "/etc/systemd/system/$SERVICE_WEB.service"
$SUDO systemctl daemon-reload
$SUDO systemctl enable --quiet "$SERVICE_API" "$SERVICE_WEB"
$SUDO systemctl restart "$SERVICE_API"; sleep 3
$SUDO systemctl restart "$SERVICE_WEB"
ok=0; for i in $(seq 1 40); do curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
[[ $ok -eq 1 ]] && c_ok "API up" || { c_err "API not answering — last log lines:"; tail -n 25 logs/api.log; exit 1; }
ok=0; for i in $(seq 1 40); do curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/login" && { ok=1; break; }; sleep 2; done
[[ $ok -eq 1 ]] && c_ok "web up" || { c_err "web not answering — last log lines:"; tail -n 25 logs/web.log; exit 1; }

# -------------------------------------------------------------- 7. nginx/https
h "7/7 Public address"
if [[ -n "$DOMAIN" ]]; then
  apt_install nginx
  SITE="/etc/nginx/sites-available/mangotree"
  $SUDO tee "$SITE" >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 210m;
    location / {
        proxy_pass http://127.0.0.1:$WEB_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 1200s;
        proxy_buffering off;
    }
}
EOF
  $SUDO ln -sf "$SITE" /etc/nginx/sites-enabled/mangotree
  if $SUDO nginx -t >/dev/null 2>&1; then
    $SUDO systemctl reload nginx; c_ok "nginx: http://$DOMAIN -> web (other sites untouched)"
  else
    $SUDO rm -f /etc/nginx/sites-enabled/mangotree; $SUDO nginx -t; die "nginx config test failed; site removed, nothing reloaded"
  fi
  if [[ -n "$EMAIL" ]]; then
    apt_install certbot python3-certbot-nginx
    if $SUDO certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect >/dev/null 2>&1; then
      c_ok "HTTPS: https://$DOMAIN (auto-renewing)"
    else
      c_warn "certbot could not issue yet (DNS not pointing here yet?). Re-run deploy.sh once it does."
    fi
  else
    c_warn "no MT_EMAIL given — HTTPS skipped. Run: MT_EMAIL=you@domain bash deploy.sh"
  fi
elif [[ "$WEB_BIND" == "0.0.0.0" ]]; then
  IP="$(curl -fsS -4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
  if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    $SUDO ufw allow "$WEB_PORT"/tcp >/dev/null && c_ok "ufw: opened port $WEB_PORT"
  fi
  c_ok "web served on the droplet's IP: http://$IP:$WEB_PORT  (plain HTTP — add a domain for HTTPS when ready)"
  c_warn "if it does not load from outside, open TCP $WEB_PORT in the DigitalOcean Cloud Firewall for this droplet"
else
  c_warn "no domain and MT_EXPOSE not set — reachable only through an SSH tunnel:  ssh -L $WEB_PORT:127.0.0.1:$WEB_PORT $RUN_USER@<droplet-ip>  then http://localhost:$WEB_PORT"
fi

printf "\n\033[1mDeployed.\033[0m  %s\n" "$([[ -n "$DOMAIN" ]] && echo "https://$DOMAIN" || { [[ "$WEB_BIND" == "0.0.0.0" ]] && echo "http://<droplet-ip>:$WEB_PORT" || echo "http://127.0.0.1:$WEB_PORT (via SSH tunnel)"; })"
cat <<EOF
  services: sudo systemctl status $SERVICE_API $SERVICE_WEB      logs: tail -f $PROJECT/logs/api.log
  update:   cd $PROJECT && git pull origin main && bash deploy.sh
EOF
