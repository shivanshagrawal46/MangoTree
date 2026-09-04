<#
.SYNOPSIS
  The one command. From this Windows PC, in the project folder:

    powershell -ExecutionPolicy Bypass -File scripts\ship.ps1 -Server root@<droplet-ip> -Dest /opt/apps/mangotree

  First time, add the public address so nginx + HTTPS are set up too:

    ... -Domain mangotree.yourdomain.com -Email you@yourdomain.com

.DESCRIPTION
  1. commits and pushes any local changes to GitHub (origin/main)
  2. on the droplet: clones the repo into -Dest the first time, then git pull
  3. sends what git must never carry: .env, client_secret.json, gmail_token.json,
     .secrets\graph_token_cache.json, and the originals (F:\MangoTree\raw_store,
     4.3 GB) - only files the droplet does not already have
  4. runs bash deploy.sh on the droplet, which handles everything else
  Re-run any time; every step is safe to repeat.
#>
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [Parameter(Mandatory = $true)][string]$Dest,
  [string]$Domain = "",
  [string]$Email = "",
  [switch]$ExposeIp,            # no domain yet: serve on http://<droplet-ip>:<port>
  [string]$Source = "F:\MangoTree\raw_store",
  [switch]$SkipOriginals,
  [string]$Message = "deploy"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
foreach ($tool in @("git", "ssh", "scp")) { if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found on this PC" } }

Write-Host "`n== 1/4 Push code to GitHub ==" -ForegroundColor Cyan
$remote = (git remote get-url origin).Trim()
git add -A | Out-Null
$pending = git status --porcelain
if ($pending) { git commit -q -m $Message; Write-Host "  committed local changes" } else { Write-Host "  nothing new to commit" }
git push -q origin main
Write-Host "  pushed origin/main"

Write-Host "`n== 2/4 Code on the droplet ==" -ForegroundColor Cyan
$remoteScript = @"
set -e
if [ ! -d '$Dest/.git' ]; then
  mkdir -p '$Dest' && git clone -q '$remote' '$Dest' && echo '  cloned into $Dest'
else
  cd '$Dest' && git fetch -q origin && git reset -q --hard origin/main && echo '  pulled origin/main'
fi
mkdir -p '$Dest/.secrets' '$Dest/raw_store' '$Dest/logs'
"@ -replace "`r", ""
ssh $Server $remoteScript
if ($LASTEXITCODE -ne 0) { throw "clone/pull failed on the droplet. If the repo is private, clone it there once by hand (git clone $remote $Dest) so credentials are stored, then re-run." }

Write-Host "`n== 3/4 Secrets and originals ==" -ForegroundColor Cyan
foreach ($f in @(".env", "client_secret.json", "gmail_token.json")) {
  if (Test-Path $f) { scp -q $f "${Server}:$Dest/$f"; Write-Host "  copied $f" } else { Write-Host "  missing $f (skipped)" -ForegroundColor Yellow }
}
if (Test-Path ".secrets\graph_token_cache.json") { scp -q ".secrets\graph_token_cache.json" "${Server}:$Dest/.secrets/graph_token_cache.json"; Write-Host "  copied .secrets\graph_token_cache.json" }
ssh $Server "chmod 600 '$Dest/.env' '$Dest/client_secret.json' '$Dest/gmail_token.json' '$Dest/.secrets/graph_token_cache.json' 2>/dev/null; true"

if (-not $SkipOriginals -and (Test-Path $Source)) {
  $files = Get-ChildItem $Source -Recurse -File
  Write-Host "  asking the droplet which originals it already has..."
  $remoteList = ssh $Server "cd '$Dest/raw_store' 2>/dev/null && find . -type f -printf '%P %s\n'" 2>$null
  $have = @{}
  foreach ($line in ($remoteList -split "`n")) { if ($line -match '^(.+) (\d+)$') { $have[$matches[1].Replace('/', '\')] = [int64]$matches[2] } }
  $todo = @($files | Where-Object { $rel = $_.FullName.Substring($Source.Length + 1); -not ($have.ContainsKey($rel) -and $have[$rel] -eq $_.Length) })
  Write-Host ("  {0:N0} originals, {1:N0} to send ({2:N2} GB)" -f $files.Count, $todo.Count, (($todo | Measure-Object -Property Length -Sum).Sum / 1GB))
  if ($todo.Count -gt 0) {
    $shards = $todo | Group-Object { $_.FullName.Substring($Source.Length + 1).Split('\')[0] }
    $i = 0
    foreach ($g in $shards) {
      $i++; $shard = $g.Name
      $tmp = Join-Path $env:TEMP "mt_ship_$shard"
      if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
      foreach ($f in $g.Group) { $rel = $f.FullName.Substring($Source.Length + 1); $dst = Join-Path $tmp $rel; New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null; Copy-Item $f.FullName $dst }
      Write-Host ("  [{0}/{1}] shard {2}: {3} files" -f $i, @($shards).Count, $shard, $g.Count)
      ssh $Server "mkdir -p '$Dest/raw_store/$shard'"
      scp -q -r "$tmp\$shard\*" "${Server}:$Dest/raw_store/$shard/"
      Remove-Item $tmp -Recurse -Force
    }
  }
} elseif ($SkipOriginals) { Write-Host "  originals skipped (-SkipOriginals)" } else { Write-Host "  originals source not found: $Source (skipped)" -ForegroundColor Yellow }

Write-Host "`n== 4/4 Deploy on the droplet ==" -ForegroundColor Cyan
$envPrefix = ""
if ($Domain) { $envPrefix += "MT_DOMAIN='$Domain' " }
if ($Email)  { $envPrefix += "MT_EMAIL='$Email' " }
ssh -t $Server "cd '$Dest' && ${envPrefix}bash deploy.sh"
if ($LASTEXITCODE -ne 0) { throw "deploy.sh reported an error (see above)" }
Write-Host "`nShipped." -ForegroundColor Green
