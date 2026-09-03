# Start MangoTree locally: API on :8000, web app on :3000.
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
# Sign in at http://localhost:3000 — users are seeded on first API start (see API log for passwords,
# or set MT_USERS="rakesh:Rakesh Sir:ceo:<pw>,jp:JP Sir:accountant:<pw>,manjunath:Manjunath Sir:operations:<pw>" in .env).
$root = Split-Path -Parent $PSScriptRoot
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; python -m uvicorn mangotree.api.app:app --port 8000 --host 127.0.0.1"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\web'; npm run dev"
Start-Sleep -Seconds 6
Start-Process "http://localhost:3000"
