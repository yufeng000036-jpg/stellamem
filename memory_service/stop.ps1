$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidPath = 'E:\Stellamem-Data\v2\service.pid'
if (!(Test-Path -LiteralPath $pidPath)) { Write-Output 'No recorded service.'; exit 0 }
$serviceId = [int][IO.File]::ReadAllText($pidPath)
$p = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceId"
if (!$p) { Write-Output 'Service already stopped.'; exit 0 }
$expected = Join-Path $taskRoot 'server.py'
if (!$p.CommandLine.Contains($expected) -or !$p.CommandLine.Contains('E:\Stellamem-Data\v2')) { throw 'PID ownership mismatch; nothing stopped.' }
Stop-Process -Id $serviceId
Write-Output 'Stopped only this memory service.'
