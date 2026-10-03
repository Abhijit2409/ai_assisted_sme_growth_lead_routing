<#
.SYNOPSIS
    Runs the analytical data platform end to end: input verification, dbt transformation layer,
    mart-to-baseline reconciliation, market-exploration regression, the market opportunity framework,
    validation of the hypothetical CRM acquisition-process design, the synthetic CRM data layer, the lead-prioritization
    and capacity-aware RM-routing layer, and dbt documentation.

.DESCRIPTION
    Safe to launch from any directory: it resolves the repository from its own location.
    It never downloads data, never rebuilds validated caches and never deletes files. Every step
    must succeed; the script stops at the first failure and exits with a nonzero code.

.PARAMETER Scope
    Full (default): the validated base analytical layer, then the market opportunity framework, then the
          CRM acquisition-process design validation, then the synthetic CRM data layer, then the lead-prioritization
          and routing layer, then the integrated acquisition-intelligence layer, then dbt docs.
    Base: only the validated base analytical layer (inputs, dbt base models and tests, reconciliation,
          market-exploration regression).
    Opportunity: only the market opportunity framework, after confirming that the base marts are current
          and the framework configuration is unchanged since it was declared.
    CRMDesign: only the validation of the hypothetical CRM acquisition-process design (catalogs in
          data/design/crm and the two CRM design documents), after confirming that the analytical platform
          is current and the market opportunity validation passed. Builds no dbt models, rebuilds no caches,
          generates no CRM records and skips dbt docs.
    CRMData: only the synthetic CRM data layer, after confirming that the analytical platform and the CRM design
          are current: generate the synthetic CRM data (validated in a temporary folder, then published), build the
          CRM staging, intermediate and foundational mart models, run the CRM dbt tests, run the independent
          validation, check two-run reproducibility and run the mutation tests. Never rebuilds public-source caches.
    AcquisitionIntelligence: only the integrated acquisition-intelligence layer, after confirming that the synthetic
          CRM data and the routing layer are current: load the reporting parameters and the KPI catalog, build the
          lead spine and the five marts, run the tagged tests, run the analysis, run the independent validation
          (which recalculates every important KPI from the source facts) and check determinism.

.PARAMETER AnalysisPython
    Python interpreter of the analysis environment (pandas, DuckDB 1.5.3). Defaults to
    $env:ANALYSIS_PYTHON, then %USERPROFILE%\anaconda3\python.exe, then "python" on PATH.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope Opportunity
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope CRMDesign
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope CRMData
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope LeadRouting
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run_analytical_pipeline.ps1 -Scope AcquisitionIntelligence
#>
[CmdletBinding()]
param(
    [ValidateSet('Full', 'Base', 'Opportunity', 'CRMDesign', 'CRMData', 'LeadRouting',
                 'AcquisitionIntelligence')][string]$Scope = 'Full',
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
    Invoke-Step 'B3. Load crosswalk and reviewed-map seeds' { & $Dbt seed --profiles-dir $Root --exclude tag:opportunity tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'B4. Build staging models' { & $Dbt run --profiles-dir $Root --select staging --exclude tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'B5. Build intermediate models' { & $Dbt run --profiles-dir $Root --select intermediate --exclude tag:opportunity tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'B6. Build marts' { & $Dbt run --profiles-dir $Root --select marts --exclude tag:opportunity tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'B7. Run all base dbt tests' { & $Dbt test --profiles-dir $Root --exclude tag:opportunity tag:crm_data tag:lead_routing tag:acquisition_intelligence }
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
    Invoke-Step 'O2. Load opportunity configuration seeds' { & $Dbt seed --profiles-dir $Root --select tag:opportunity --exclude tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'O3. Build opportunity models' { & $Dbt run --profiles-dir $Root --select tag:opportunity --exclude tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'O4. Run opportunity dbt tests' { & $Dbt test --profiles-dir $Root --select tag:opportunity --exclude tag:crm_data tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'O5. Opportunity framework analysis' { & $AnalysisPython 'src\analysis\13_analyze_market_opportunity_framework.py' }
    Invoke-Step 'O6. Opportunity framework validation (recalculation, eligibility, sensitivity, two-run reproducibility)' {
        & $AnalysisPython 'src\validation\14_validate_market_opportunity_framework.py'
    }
}

if ($Scope -in @('Full', 'CRMDesign')) {
    Write-Host ''
    Write-Host '--- CRM acquisition-process design (hypothetical; no CRM records) ---' -ForegroundColor Yellow
    Invoke-Step 'C1. Confirm the analytical platform is current (base marts and opportunity configuration)' {
        & $AnalysisPython 'src\validation\14_validate_market_opportunity_framework.py' --preconditions-only
    }
    Invoke-Step 'C2. Confirm the market opportunity validation passed and the design catalogs are present' {
        & $AnalysisPython 'src\validation\15_validate_crm_design.py' --preconditions-only
    }
    Invoke-Step 'C3. CRM design validation (catalogs, state machine, references, traceability, scenarios, documents, privacy)' {
        & $AnalysisPython 'src\validation\15_validate_crm_design.py'
    }
}

if ($Scope -in @('Full', 'CRMData')) {
    Write-Host ''
    Write-Host '--- Synthetic CRM data (fictional; simulation settings, not bank policy) ---' -ForegroundColor Yellow
    if ($Scope -eq 'CRMData') {
        Invoke-Step 'D1. Confirm the analytical platform is current (base marts and opportunity configuration)' {
            & $AnalysisPython 'src\validation\14_validate_market_opportunity_framework.py' --preconditions-only
        }
        Invoke-Step 'D2. Confirm the CRM design is valid (design catalogs, change log, documents)' {
            & $AnalysisPython 'src\validation\15_validate_crm_design.py'
        }
    }
    Invoke-Step 'D3. Generate synthetic CRM data (validated in a temporary folder, then published)' {
        & $AnalysisPython 'src\synthetic\16_generate_synthetic_crm_data.py'
    }
    Invoke-Step 'D4. Build CRM staging models' { & $Dbt run --profiles-dir $Root --select path:models/staging/crm }
    Invoke-Step 'D5. Build CRM intermediate models' { & $Dbt run --profiles-dir $Root --select path:models/intermediate/crm }
    Invoke-Step 'D6. Build foundational CRM marts' { & $Dbt run --profiles-dir $Root --select path:models/marts/crm }
    Invoke-Step 'D7. Run CRM dbt tests' { & $Dbt test --profiles-dir $Root --select tag:crm_data --exclude tag:lead_routing tag:acquisition_intelligence }
    Invoke-Step 'D8. Independent synthetic CRM validation (sources, staging reconciliation, grains, rules, scenarios, privacy)' {
        & $AnalysisPython 'src\validation\17_validate_synthetic_crm_data.py'
    }
    Invoke-Step 'D9. Two-run reproducibility of the synthetic CRM data' {
        & $AnalysisPython 'src\synthetic\16_generate_synthetic_crm_data.py' --check-reproducibility
    }
    Invoke-Step 'D10. Mutation tests of the synthetic CRM validation (temporary copies only)' {
        & $AnalysisPython 'src\validation\17_validate_synthetic_crm_data.py' --mutation-tests
    }
}

if ($Scope -in @('Full', 'LeadRouting')) {
    Write-Host ''
    Write-Host '--- Lead prioritization and capacity-aware RM routing (hypothetical rules; synthetic data) ---' -ForegroundColor Yellow
    if ($Scope -eq 'LeadRouting') {
        Invoke-Step 'L1. Confirm the analytical platform is current (base marts and opportunity configuration)' {
            & $AnalysisPython 'src\validation\14_validate_market_opportunity_framework.py' --preconditions-only
        }
        Invoke-Step 'L2. Confirm the CRM design is valid (design catalogs, change log, documents)' {
            & $AnalysisPython 'src\validation\15_validate_crm_design.py'
        }
        Invoke-Step 'L3. Confirm the synthetic CRM data are current (manifest hashes and the recorded validation)' {
            & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py' --preconditions-only
        }
    }
    Invoke-Step 'L4. Verify the declared configuration hashes' {
        & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py' --config-hashes-only
    }
    Invoke-Step 'L5. Load the lead-routing configuration seeds' { & $Dbt seed --profiles-dir $Root --select tag:lead_routing }
    Invoke-Step 'L6. Build the priority and routing models' { & $Dbt run --profiles-dir $Root --select tag:lead_routing }
    Invoke-Step 'L7. Run the lead-routing dbt tests' { & $Dbt test --profiles-dir $Root --select tag:lead_routing --exclude tag:acquisition_intelligence }
    Invoke-Step 'L8. Lead prioritization and routing analysis (sensitivity, policy comparison, diagnostics, figures)' {
        & $AnalysisPython 'src\analysis\18_analyze_lead_prioritization_and_routing.py'
    }
    Invoke-Step 'L9. Independent validation (recomputation, leakage lineage, capacity as-of, ranking, queues, evaluation)' {
        & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py'
    }
    Invoke-Step 'L10. Mutation tests of the lead-routing validation (isolated copies only)' {
        & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py' --mutation-tests
    }
    Invoke-Step 'L11. Repeated-build reproducibility of the priority and routing layer' {
        & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py' --check-reproducibility
    }
}

if ($Scope -in @('Full', 'AcquisitionIntelligence')) {
    Write-Host ''
    Write-Host '--- Integrated acquisition intelligence (hypothetical rules; synthetic data) ---' -ForegroundColor Yellow
    if ($Scope -eq 'AcquisitionIntelligence') {
        Invoke-Step 'A1. Confirm the synthetic CRM data and the routing layer are current' {
            & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py' --preconditions-only
        }
        Invoke-Step 'A2. Verify the declared routing configuration hashes' {
            & $AnalysisPython 'src\validation\19_validate_lead_prioritization_and_routing.py' --config-hashes-only
        }
    }
    Invoke-Step 'A3. Load the acquisition reporting parameters and the KPI catalog' {
        & $Dbt seed --profiles-dir $Root --select tag:acquisition_intelligence
    }
    Invoke-Step 'A4. Build the lead spine and the acquisition-intelligence marts' {
        & $Dbt run --profiles-dir $Root --select tag:acquisition_intelligence
    }
    Invoke-Step 'A5. Run the acquisition-intelligence dbt tests' {
        & $Dbt test --profiles-dir $Root --select tag:acquisition_intelligence
    }
    Invoke-Step 'A6. Acquisition intelligence analysis (market focus, funnel, capacity, actions, figures)' {
        & $AnalysisPython 'src\analysis\20_analyze_acquisition_intelligence.py'
    }
    Invoke-Step 'A7. Independent validation (KPI recalculation from source facts, reconciliation, fan-out control)' {
        & $AnalysisPython 'src\validation\21_validate_acquisition_intelligence.py'
    }
    Invoke-Step 'A8. Determinism of the acquisition-intelligence validation' {
        & $AnalysisPython 'src\validation\21_validate_acquisition_intelligence.py' --determinism
    }
}

if ($Scope -ne 'CRMDesign') {
    Invoke-Step 'Generate dbt documentation' { & $Dbt docs generate --profiles-dir $Root }
}

Write-Host ''
Write-Host "Analytical pipeline completed (scope: $Scope): every step passed." -ForegroundColor Green
exit 0
