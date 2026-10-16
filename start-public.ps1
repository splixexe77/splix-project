<#
.SYNOPSIS
  Put the SPLIX X CHEAT site on a free public URL - no card, no account.

.DESCRIPTION
  Uses the official Cloudflare quick tunnel
  (https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/):
  one command, random https://*.trycloudflare.com hostname, no signup.

  Steps:
    1. Reads server/.env (creates it from .env.example if missing) to
       learn the local PORT (default 8099).
    2. Stops any previous cloudflared process (quick-tunnel hostnames
       rotate on every run, so there must be only one).
    3. Starts the tunnel and captures the URL it prints.
    4. Backs up server/.env to server/.env.<stamp>.bak (the name ends in
       .bak, so .gitignore keeps it out of git), then sets
       PUBLIC_URL=<tunnel url> and TRUST_PROXY=true.  TRUST_PROXY keeps
       per-visitor rate limiting correct behind the proxy: the proxy
       appends the visitor IP to X-Forwarded-For, which is exactly what
       the rightmost-hop parser expects (spoof-proof, already tested).
    5. Restarts the local server so the new values take effect, waits
       for /health on localhost, then checks /health through the
       public URL.

  Nothing from .env is ever printed except the public URL (which the
  tunnel itself prints) and whether ADMIN_TOKEN is set.

  Stop the tunnel:    Get-Process cloudflared | Stop-Process
  Back to localhost:  set PUBLIC_URL=http://127.0.0.1:8099 and
                      TRUST_PROXY=false in server/.env, restart server.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\start-public.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$serverDir = Join-Path $root 'server'
$envPath = Join-Path $serverDir '.env'
$examplePath = Join-Path $serverDir '.env.example'
$boot = Join-Path $serverDir 'run.py'

function Note([string]$msg) { Write-Host ('  -> ' + $msg) }

# ---- 1. read local config ----------------------------------------
if (-not (Test-Path $envPath)) {
  if (-not (Test-Path $examplePath)) { throw 'server\.env and server\.env.example are both missing.' }
  Copy-Item $examplePath $envPath
  Note 'created server\.env from .env.example (set ADMIN_TOKEN in it if you want the admin API)'
}
$port = 8099
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
  if ($line -match '^PORT=([0-9]+)') { $port = [int]$Matches[1] }
}
$base = 'http://127.0.0.1:' + $port
Note ('local target ' + $base)

# ---- 2. exactly one tunnel ---------------------------------------
$old = @(Get-Process cloudflared -ErrorAction SilentlyContinue)
if ($old.Count -gt 0) {
  $old | Stop-Process -Force
  Note ('stopped previous tunnel (PID ' + (($old | ForEach-Object { $_.Id }) -join ',') + ')')
}

# ---- 3. start a fresh free tunnel --------------------------------
$outLog = Join-Path $env:TEMP 'splix_tunnel_out.log'
$errLog = Join-Path $env:TEMP 'splix_tunnel_err.log'
Remove-Item $outLog, $errLog -Force -ErrorAction SilentlyContinue
Start-Process -FilePath 'cloudflared' -ArgumentList @('tunnel', '--url', $base, '--output', 'json') -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Note 'starting free Cloudflare quick tunnel (no account, no card)...'

$url = ''
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 1
  $buf = ''
  foreach ($f in @($outLog, $errLog)) {
    if (Test-Path $f) {
      try { $buf += [IO.File]::ReadAllText($f) } catch { }
    }
  }
  $m = [regex]::Match($buf, 'https://[a-z0-9\-]+\.trycloudflare\.com')
  if ($m.Success) { $url = $m.Value; break }
}
if (-not $url) {
  $hint = ''
  if (Test-Path (Join-Path (Join-Path $env:USERPROFILE '.cloudflared') 'config.yaml')) {
    $hint = ' (cloudflared config.yaml exists in ~\.cloudflared - quick tunnels do not support it; see the Cloudflare docs)'
  }
  throw ('no trycloudflare URL after 60s - check ' + $errLog + $hint)
}
Note ('public URL ' + $url)

# ---- 4. back up and update .env ----------------------------------
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
Copy-Item $envPath ($envPath + '.' + $stamp + '.bak')
Note ('backed up server/.env -> .env.' + $stamp + '.bak')

$lines = New-Object 'System.Collections.Generic.List[string]'
$sawPublicUrl = $false
$sawTrustProxy = $false
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
  if ($line -like 'PUBLIC_URL=*') { $lines.Add('PUBLIC_URL=' + $url); $sawPublicUrl = $true }
  elseif ($line -like 'TRUST_PROXY=*') { $lines.Add('TRUST_PROXY=true'); $sawTrustProxy = $true }
  else { $lines.Add($line) }
}
if (-not $sawPublicUrl) { $lines.Add('PUBLIC_URL=' + $url) }
if (-not $sawTrustProxy) { $lines.Add('TRUST_PROXY=true') }
[IO.File]::WriteAllLines($envPath, $lines)
Note 'server/.env updated: PUBLIC_URL=<tunnel url>, TRUST_PROXY=true'

# ---- 5. restart the server with the new values -------------------
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
  $oldPid = $listener.OwningProcess
  Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
  Note ('stopped old server (PID ' + $oldPid + ')')
  Start-Sleep -Milliseconds 700
}
Start-Process -FilePath 'python' -ArgumentList @($boot) -WorkingDirectory $root -WindowStyle Hidden
Note 'starting server...'

$localOk = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $r = Invoke-WebRequest -Uri ($base + '/health') -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $localOk = $true; break }
  } catch { }
}
if (-not $localOk) { throw ('server did not answer ' + $base + '/health') }
Note 'local /health: 200'

# ---- 6. verify through the public URL ----------------------------
$publicOk = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest -Uri ($url + '/health') -UseBasicParsing -TimeoutSec 6
    if ($r.StatusCode -eq 200) { $publicOk = $true; break }
  } catch { }
}

# ---- summary ------------------------------------------------------
$adminSet = $false
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
  if ($line -like 'ADMIN_TOKEN=*') {
    if ($line -ne 'ADMIN_TOKEN=') { $adminSet = $true }
    break
  }
}
Write-Host ''
Write-Host ('  PUBLIC URL: ' + $url) -ForegroundColor Green
if ($publicOk) {
  Write-Host '  /health through the tunnel: 200 OK' -ForegroundColor Green
} else {
  Write-Host '  /health through the tunnel: not reachable yet (the edge needs a few seconds - rerun or wait).' -ForegroundColor Yellow
}
if (-not $adminSet) {
  Write-Host '  ADMIN_TOKEN is empty in server/.env - the admin API stays disabled until you set one.' -ForegroundColor Yellow
}
Write-Host ('  tunnel log: ' + $errLog)
Write-Host '  stop:       Get-Process cloudflared | Stop-Process'
Write-Host '  The URL changes on every rerun. With Google OAuth, update the redirect URI in Google Cloud Console.'
