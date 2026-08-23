<#
Wrapper that Task Scheduler runs to refresh the README "visibility changes" badge.

This matches the arrangement already used for the Newsflasharr and IPTV Checker
badges: one task per plugin, each running that plugin's own wrapper from its own
repository. Nothing here reaches into another project.

WHY A WRAPPER. A scheduled task starts with a minimal environment, so anything
resolved from PATH in an interactive shell may not resolve here. Both tools this
needs are pinned by absolute path below. Docker's directory is prepended to PATH
because update_events_badge.py calls `docker` by name.

WHAT IT DOES. Runs scripts/update_events_badge.py, appends a timestamped record of
the run to dist/badge-update.log, and exits with the script's own exit code so
Task Scheduler's "Last Run Result" reflects a failure rather than reporting
success for a run that did nothing.

IT ONLY WORKS WHILE THE USER IS LOGGED ON. The task is registered to run in the
interactive user session on purpose. Docker Desktop runs in that session and the
GitHub CLI reads its token from the user keyring, so a run with nobody logged on
would fail rather than silently publish a stale number.

A RUN THAT REPORTS "updated gist" MAY CHANGE NOTHING, and that is correct:
identical content creates no new revision. The raw Gist URL is cached for about
five minutes, and Shields caches on top of that, so a new number is not visible
the instant this finishes.
#>
$ErrorActionPreference = 'Stop'

$Python = 'C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe'
$DockerBin = 'C:\Program Files\Docker\Docker\resources\bin'
$Repo = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'update_events_badge.py'
$LogDir = Join-Path $Repo 'dist'
$Log = Join-Path $LogDir 'badge-update.log'
$MaxLogBytes = 1MB

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory $LogDir | Out-Null }

# Rotate before writing, keeping one previous file. An unbounded log on a task
# that runs twice a day is a slow disk leak nobody would notice.
if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt $MaxLogBytes)) {
    Move-Item $Log "$Log.1" -Force
}

$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
Add-Content -Path $Log -Encoding utf8 -Value "=== $stamp run start ==="

$env:PATH = "$DockerBin;$env:PATH"

# The invocation itself can fail before the script produces any output, for
# example when the pinned python path is wrong after an interpreter upgrade.
# Without this catch the wrapper would die with the reason on a console nobody is
# watching, leaving a log that ends mid-run and says nothing about why.
try {
    $output = & $Python $Script 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) { Add-Content -Path $Log -Encoding utf8 -Value $line }
} catch {
    Add-Content -Path $Log -Encoding utf8 -Value "wrapper FAILED before the script ran: $($_.Exception.Message)"
    $code = 1
}

Add-Content -Path $Log -Encoding utf8 -Value "=== exit $code ==="

exit $code
