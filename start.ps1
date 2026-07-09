# Start grokcli-2api on Windows and open the admin web UI
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Optional overrides:
#   $env:GROK2API_PORT = "3000"
#   $env:GROK2API_HOST = "127.0.0.1"
#   $env:GROK2API_OPEN_BROWSER = "1"   # set 0 to disable auto-open
#   $env:GROK2API_API_KEY = "sk-local"
#   $env:GROK2API_DEFAULT_MODEL = "grok-4.5"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "python not found in PATH. Install Python 3.10+ first."
}

python -c "import fastapi, uvicorn, httpx" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies..."
    pip install -r requirements.txt
}

if (-not $env:GROK2API_OPEN_BROWSER) { $env:GROK2API_OPEN_BROWSER = "1" }
if (-not $env:GROK2API_HOST) { $env:GROK2API_HOST = "127.0.0.1" }
if (-not $env:GROK2API_PORT) { $env:GROK2API_PORT = "3000" }

$port = $env:GROK2API_PORT
Write-Host "Starting grokcli-2api..."
Write-Host "  Admin: http://127.0.0.1:$port/admin"
Write-Host "  (browser opens automatically unless GROK2API_OPEN_BROWSER=0)"

python app.py
