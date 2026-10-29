$ErrorActionPreference = "Stop"
$env:VITE_DEV_PORT = "5188"
$env:VITE_HMR_CLIENT_PORT = "5188"
Set-Location $PSScriptRoot
& (Join-Path $PSScriptRoot "node_modules\.bin\vite.cmd") --host 127.0.0.1 --port 5188 --strictPort
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
