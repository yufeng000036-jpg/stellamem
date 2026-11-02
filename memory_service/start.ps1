$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = 'E:\Stellamem-Data\v2'
$pythonExe = 'E:\Python\python.exe'
$port = 8792
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
try {
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
  if ($health.version -eq 2) { Write-Output "Already running: http://127.0.0.1:$port"; exit 0 }
  throw 'Port is already used by a different service.'
} catch {
  if ($_.Exception.Message -like '*different service*') { throw }
}
$serviceFile = Join-Path $taskRoot 'server.py'
$p = Start-Process -FilePath $pythonExe -ArgumentList @('-B', $serviceFile, '--data', $dataRoot, '--port', "$port") -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dataRoot 'server.stdout.log') -RedirectStandardError (Join-Path $dataRoot 'server.stderr.log') -PassThru
for ($i=0; $i -lt 60; $i++) {
  Start-Sleep -Milliseconds 250
  if ($p.HasExited) { throw 'Startup failed. Inspect the E-drive server.stderr.log.' }
  try {
    $health=Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 1
    if ($health.version -eq 2) { Write-Output "Ready: http://127.0.0.1:$port"; exit 0 }
  } catch {}
}
throw 'Startup timed out; no readiness claim made.'
