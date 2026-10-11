# Convert every .jcl file in a folder, one job at a time.
#
# Run from any directory. Relative paths are resolved from the repository root
# rather than the caller's current directory. Activate the project virtual
# environment first, or pass its Python executable through -PythonCommand.
#
# Examples:
#   .\scripts\run_all_jcl.ps1
#   .\scripts\run_all_jcl.ps1 -JclFolder jcl -CntlFolder cntl
#   .\scripts\run_all_jcl.ps1 -JclFolder C:\data\jobs -OutputFolder outputs
#
# agent.main creates outputs/<job>/<run_id>/ for each JCL. This wrapper keeps
# a separate console log per job, continues after a failed or incomplete job,
# and exits with code 1 if any job did not succeed.

param(
    [string]$JclFolder = '',
    [string]$CntlFolder = 'cntl',
    [string]$OutputFolder = 'outputs',
    [string]$LogFolder = '',
    [string]$PythonCommand = 'python'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Resolve-ProjectPath {
    param([Parameter(Mandatory = $true)][string]$Value)

    # GetFullPath does not require the target to exist. That lets us validate
    # input folders and create output folders in separate, clear steps.
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}

# usable without editing this script. An explicit -JclFolder overrides both.
if ([string]::IsNullOrWhiteSpace($JclFolder)) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot 'jcl') -PathType Container) {
        $JclFolder = 'jcl'
    } else {
        $JclFolder = 'jcl'
    }
}

$jclDir = Resolve-ProjectPath $JclFolder
$cntlDir = Resolve-ProjectPath $CntlFolder
$outputDir = Resolve-ProjectPath $OutputFolder
$batchId = '{0}-{1}' -f (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'), $PID
if ([string]::IsNullOrWhiteSpace($LogFolder)) {
    $logDir = Join-Path $outputDir (Join-Path 'batch_logs' $batchId)
} else {
    $logDir = Resolve-ProjectPath $LogFolder
}

# Missing shared prerequisites are batch setup errors. A problem with one
# specific JCL is handled later without stopping the remaining jobs.
if (-not (Test-Path -LiteralPath $jclDir -PathType Container)) {
    Write-Error "JCL folder not found: $jclDir"
    exit 2
}
if (-not (Test-Path -LiteralPath $cntlDir -PathType Container)) {
    Write-Error "CNTL folder not found: $cntlDir"
    exit 2
}
if (-not (Get-Command $PythonCommand -ErrorAction SilentlyContinue)) {
    Write-Error "Python executable not found: $PythonCommand"
    exit 2
}

# Sort by full path for a repeatable sequence. Numbered log names avoid
# collisions when separate source folders contain the same JCL file name.
$jclFiles = @(
    Get-ChildItem -LiteralPath $jclDir -File -Recurse |
        Where-Object { $_.Extension -ieq '.jcl' } |
        Sort-Object FullName
)
if ($jclFiles.Count -eq 0) {
    Write-Error "No .jcl files found in: $jclDir"
    exit 2
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$rows = New-Object System.Collections.Generic.List[object]
$succeeded = 0
$failed = 0
$sequence = 0

# Running from the repository root makes `python -m agent.main` import the
# local agent package. Always restore the caller's location afterward.
Push-Location -LiteralPath $projectRoot
try {
    foreach ($jcl in $jclFiles) {
        $sequence++
        $logFile = Join-Path $logDir ('{0:D4}.log' -f $sequence)
        Write-Host ('[{0}/{1}] {2}' -f $sequence, $jclFiles.Count, $jcl.FullName)

        # Redirect all console streams into this job's log. The CLI returns
        # nonzero for a failed or incomplete run. We record that exit code and
        # continue, giving every JCL a separate result and run ID.
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $PythonCommand -m agent.main $jcl.FullName `
                --cntl-folder $cntlDir `
                --output-folder $outputDir *> $logFile
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }

        if ($exitCode -eq 0) {
            $succeeded++
            Write-Host "  SUCCESS  log: $logFile"
        } else {
            $failed++
            Write-Host "  NON-SUCCESS (exit $exitCode)  log: $logFile"
        }
        $rows.Add([pscustomobject]@{
            sequence = $sequence
            jcl = $jcl.FullName
            exit_code = $exitCode
            log = $logFile
        })
    }
} finally {
    Pop-Location
}

# Keep one manifest for the whole batch. The generated run folders and the
# SQLite index remain in OutputFolder; this CSV is only a navigation aid.
$summary = Join-Path $logDir 'summary.csv'
$rows | Export-Csv -LiteralPath $summary -NoTypeInformation -Encoding UTF8
Write-Host ('Completed: {0} total, {1} successful, {2} non-successful' -f `
    $jclFiles.Count, $succeeded, $failed)
Write-Host "Batch manifest: $summary"
if ($failed -gt 0) {
    exit 1
}
exit 0
