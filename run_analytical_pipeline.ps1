<#
.SYNOPSIS
    Runs the analytical data platform end to end: input verification, dbt transformation layer,
    mart-to-baseline reconciliation, market-exploration regression and dbt documentation.

.DESCRIPTION
    Safe to launch from any directory: it resolves the repository from its own location.
    It never downloads data, never rebuilds validated caches and never deletes files. Every step
    must succeed; the script stops at the first failure and exits with a nonzero code.

.PARAMETER AnalysisPython
    Python interpreter of the analysis environment (pandas, DuckDB 1.5.3). Defaults to
    $env:ANALYSIS_PYTHON, then %USERPROFILE%\anaconda3\python.exe, then "python" on PATH.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1
#>
[CmdletBinding()]
param([string]$AnalysisPython)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (-not $AnalysisPython) {
    if ($env:ANALYSIS_PYTHON) { $AnalysisPython = $env:ANALYSIS_PYTHON }
    elseif (Test-Path -LiteralPath (Join-Path $env:USERPROFILE 'anaconda3\python.exe')) { $AnalysisPython = Join-Path $env:USERPROFILE 'anaconda3\python.exe' }
    else { $AnalysisPython = 'python' }
}
$Dbt = Join-Path $Root '.venv\Scripts\dbt.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host ''
    Write-Host "==> $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit code $LASTEXITCODE). Pipeline stopped." -ForegroundColor Red
        exit 1
    }
    Write-Host "OK: $Name" -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $Dbt)) {
    Write-Host "dbt not found at $Dbt. Create the dbt environment first (see README: Installation)." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'profiles.yml'))) {
    Copy-Item -LiteralPath (Join-Path $Root 'profiles.example.yml') -Destination (Join-Path $Root 'profiles.yml')
    Write-Host 'Created profiles.yml from profiles.example.yml (local DuckDB path, no credentials).'
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\warehouse') | Out-Null

Invoke-Step '1-3. Verify raw sources, cache lineage, validation summaries, analytical-view and market-exploration baselines' {
    & $AnalysisPython 'src\validation\verify_pipeline_inputs.py'
}
Write-Host ''
Write-Host '==> 4. dbt packages: none required (no packages.yml)' -ForegroundColor Cyan
Invoke-Step 'dbt debug' { & $Dbt debug --profiles-dir $Root }
Invoke-Step '5. Load crosswalk and reviewed-map seeds' { & $Dbt seed --profiles-dir $Root }
Invoke-Step '6. Build staging models' { & $Dbt run --profiles-dir $Root --select staging }
Invoke-Step '7. Build intermediate models' { & $Dbt run --profiles-dir $Root --select intermediate }
Invoke-Step '8. Build marts' { & $Dbt run --profiles-dir $Root --select marts }
Invoke-Step '9. Run all dbt tests' { & $Dbt test --profiles-dir $Root }
Invoke-Step '10. Reconcile the 11 marts with the baseline Parquet views' { & $AnalysisPython 'src\validation\11_reconcile_dbt_marts.py' }
Invoke-Step '11-14. Market exploration from dbt marts: two runs, regression, determinism, in-place refresh' {
    & $AnalysisPython 'src\validation\12_verify_market_exploration_regression.py'
}
Invoke-Step '15. Generate dbt documentation' { & $Dbt docs generate --profiles-dir $Root }

Write-Host ''
Write-Host 'Analytical pipeline completed: every step passed.' -ForegroundColor Green
exit 0
