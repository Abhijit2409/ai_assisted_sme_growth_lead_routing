<#
.SYNOPSIS
    Runs the analytical data platform end to end: input verification, dbt transformation layer,
    mart-to-baseline reconciliation, market-exploration regression, the market opportunity framework
    and dbt documentation.

.DESCRIPTION
    Safe to launch from any directory: it resolves the repository from its own location.
    It never downloads data, never rebuilds validated caches and never deletes files. Every step
    must succeed; the script stops at the first failure and exits with a nonzero code.

.PARAMETER Scope
    Full (default): the validated base analytical layer, then the market opportunity framework, then dbt docs.
    Base: only the validated base analytical layer (inputs, dbt base models and tests, reconciliation,
          market-exploration regression).
    Opportunity: only the market opportunity framework, after confirming that the base marts are current
          and the framework configuration is unchanged since it was declared.

.PARAMETER AnalysisPython
    Python interpreter of the analysis environment (pandas, DuckDB 1.5.3). Defaults to
    $env:ANALYSIS_PYTHON, then %USERPROFILE%\anaconda3\python.exe, then "python" on PATH.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope Opportunity
#>
[CmdletBinding()]
param(
    [ValidateSet('Full', 'Base', 'Opportunity')][string]$Scope = 'Full',
    [string]$AnalysisPython
)

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

Write-Host "Scope: $Scope"
Invoke-Step 'dbt debug' { & $Dbt debug --profiles-dir $Root }

if ($Scope -in @('Full', 'Base')) {
    Write-Host ''
    Write-Host '--- Validated base analytical layer ---' -ForegroundColor Yellow
    Invoke-Step 'B1. Verify raw sources, cache lineage, validation summaries, analytical-view and market-exploration baselines' {
        & $AnalysisPython 'src\validation\verify_pipeline_inputs.py'
    }
    Write-Host ''
    Write-Host '==> B2. dbt packages: none required (no packages.yml)' -ForegroundColor Cyan
    Invoke-Step 'B3. Load crosswalk and reviewed-map seeds' { & $Dbt seed --profiles-dir $Root --exclude tag:opportunity }
    Invoke-Step 'B4. Build staging models' { & $Dbt run --profiles-dir $Root --select staging }
    Invoke-Step 'B5. Build intermediate models' { & $Dbt run --profiles-dir $Root --select intermediate --exclude tag:opportunity }
    Invoke-Step 'B6. Build marts' { & $Dbt run --profiles-dir $Root --select marts --exclude tag:opportunity }
    Invoke-Step 'B7. Run all base dbt tests' { & $Dbt test --profiles-dir $Root --exclude tag:opportunity }
    Invoke-Step 'B8. Reconcile the 11 marts with the baseline Parquet views' { & $AnalysisPython 'src\validation\11_reconcile_dbt_marts.py' }
    Invoke-Step 'B9. Market exploration from dbt marts: two runs, regression, determinism, in-place refresh' {
        & $AnalysisPython 'src\validation\12_verify_market_exploration_regression.py'
    }
}

if ($Scope -in @('Full', 'Opportunity')) {
    Write-Host ''
    Write-Host '--- Market opportunity framework ---' -ForegroundColor Yellow
    Invoke-Step 'O1. Confirm base marts are current and the framework configuration is unchanged' {
        & $AnalysisPython 'src\validation\14_validate_market_opportunity_framework.py' --preconditions-only
    }
    Invoke-Step 'O2. Load opportunity configuration seeds' { & $Dbt seed --profiles-dir $Root --select tag:opportunity }
    Invoke-Step 'O3. Build opportunity models' { & $Dbt run --profiles-dir $Root --select tag:opportunity }
    Invoke-Step 'O4. Run opportunity dbt tests' { & $Dbt test --profiles-dir $Root --select tag:opportunity }
    Invoke-Step 'O5. Opportunity framework analysis' { & $AnalysisPython 'src\analysis\13_analyze_market_opportunity_framework.py' }
    Invoke-Step 'O6. Opportunity framework validation (recalculation, eligibility, sensitivity, two-run reproducibility)' {
        & $AnalysisPython 'src\validation\14_validate_market_opportunity_framework.py'
    }
}

Invoke-Step 'Generate dbt documentation' { & $Dbt docs generate --profiles-dir $Root }

Write-Host ''
Write-Host "Analytical pipeline completed (scope: $Scope): every step passed." -ForegroundColor Green
exit 0
