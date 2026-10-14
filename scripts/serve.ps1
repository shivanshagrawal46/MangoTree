# Run MangoTree the fast way: the API on :8000 and the PRODUCTION build of the web app on :3000.
#   powershell -ExecutionPolicy Bypass -File scripts\serve.ps1          # build once, then serve
#   powershell -ExecutionPolicy Bypass -File scripts\serve.ps1 -NoBuild # serve the last build
# `scripts\dev.ps1` runs `next dev`, which compiles every page the first time you open it (slow clicks,
# blank pages while it compiles). This script serves compiled pages: every click answers at once.
param([switch]$NoBuild, [int]$Port = 3000)
$root = Split-Path -Parent $PSScriptRoot
if (-not $NoBuild) {
  Push-Location "$root\web"
  npm run build
  if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "web build failed"; exit 1 }
  Pop-Location
}
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; python -m uvicorn mangotree.api.app:app --port 8000 --host 127.0.0.1"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\web'; npx next start -p $Port"
Start-Sleep -Seconds 5
Start-Process "http://localhost:$Port/arv"
