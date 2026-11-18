# pktmon_probe.ps1 - start a pktmon capture on the game's port.
# Must run elevated. Writes its own log because the elevated child does not
# share the parent console.
$log = "D:\Projects\RocoAgent\logs\pktmon.txt"
"start $(Get-Date -Format 'HH:mm:ss') admin=$([bool]([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))" | Out-File $log -Encoding utf8

pktmon filter remove 2>&1 | Out-File $log -Append -Encoding utf8
pktmon filter add -p 8195 2>&1 | Out-File $log -Append -Encoding utf8
$etl = "$env:TEMP\roco.etl"
Remove-Item $etl -ErrorAction SilentlyContinue
pktmon start --capture --pkt-size 0 --file $etl 2>&1 | Out-File $log -Append -Encoding utf8
Start-Sleep -Seconds 2
pktmon status 2>&1 | Out-File $log -Append -Encoding utf8
