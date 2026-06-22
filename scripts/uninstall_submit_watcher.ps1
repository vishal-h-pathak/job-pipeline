<#
.SYNOPSIS
  uninstall_submit_watcher.ps1 — remove the Windows submit-watcher auto-start
  (feat/dual-machine-watcher). Windows parity of
  scripts/uninstall_submit_watcher.sh.

.DESCRIPTION
  Stops and unregisters the scheduled task installed by
  install_submit_watcher.ps1. The generated wrapper .cmd and log file under
  %LOCALAPPDATA%\jobpipe are left in place by default (the log is useful
  post-mortem); pass -Purge to delete them too.

  Safe to run when nothing is installed — it reports "not found" and exits 0.

.PARAMETER Purge
  Also delete the wrapper .cmd and submit-watch.log under %LOCALAPPDATA%\jobpipe.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\uninstall_submit_watcher.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [switch]$Purge
)

$ErrorActionPreference = "Stop"
$TaskName = "io.thak.jobpipe.submit-watch"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
  if ($PSCmdlet.ShouldProcess($TaskName, "Stop and unregister scheduled task")) {
    # Stop any running instance first; ignore if it isn't running.
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "✓ Unregistered scheduled task '$TaskName'." -ForegroundColor Green
  }
} else {
  Write-Host "Task '$TaskName' not found — nothing to remove."
}

if ($Purge) {
  $AppDataDir = Join-Path $env:LOCALAPPDATA "jobpipe"
  foreach ($f in @("run-submit-watch.cmd", "submit-watch.log")) {
    $p = Join-Path $AppDataDir $f
    if (Test-Path $p) {
      if ($PSCmdlet.ShouldProcess($p, "Delete")) {
        Remove-Item $p -Force
        Write-Host "Deleted $p"
      }
    }
  }
}
