# Windows PowerShell shortcut for the cross-platform dev controller (same commands as `make`).
#   .\dev.ps1 bootstrap [--train]   (new machine: setup + tests + all scenarios + start + load)
#   .\dev.ps1 setup | start | stop | status | logs | up | down | seed | load FIN-001 FIN-003 | test | eval
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\dev.ps1 start
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $py "$PSScriptRoot\scripts\dev\devctl.py" @args
exit $LASTEXITCODE
