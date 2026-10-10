<#
.SYNOPSIS
  Dry-run-default Google Cloud provisioning plan (design §10.4, FR-C5, AC-38).

  Prints a resource-by-resource plan with estimated monthly cost and a total,
  and performs NO cloud mutation unless -Confirm is passed. Every mutating gcloud
  call (including `services enable`) is routed through Invoke-Planned, which is a
  no-op without -Confirm. The implementer NEVER runs this with -Confirm.

  Project guard: refuses an empty project id, refuses a mismatch against the
  active gcloud project without -Force, and UNCONDITIONALLY refuses
  fablecraft-4ab6c (an unrelated application) even with -Force.
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "",
  [string]$Region = "asia-south1",
  [switch]$Confirm,
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$script:Planned = @()
$FORBIDDEN_PROJECT = "fablecraft-4ab6c"

$pricingPath = Join-Path $PSScriptRoot "cost\pricing-inputs.json"
$pricing = if (Test-Path $pricingPath) { Get-Content $pricingPath -Raw | ConvertFrom-Json } else { $null }

function Add-Planned {
  param($Kind, $Name, $Command, $MonthlyUsd, $Note)
  $script:Planned += [pscustomobject]@{ Kind = $Kind; Name = $Name; Command = $Command; MonthlyUsd = $MonthlyUsd; Note = $Note }
}

function Invoke-Planned {
  param($Kind, $Name, $Command, $MonthlyUsd = 0.0, $Note = "")
  Add-Planned -Kind $Kind -Name $Name -Command $Command -MonthlyUsd $MonthlyUsd -Note $Note
  if (-not $Confirm) { return }   # the single gate; dry-run is the default
  Write-Host "EXECUTING: $Command"
  Invoke-Expression $Command
  if ($LASTEXITCODE -ne 0) { throw "failed: $Name" }
}

function Assert-ProjectSafe {
  if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    throw "ProjectId is required. Refusing to act with an empty project id."
  }
  if ($ProjectId -eq $FORBIDDEN_PROJECT) {
    throw "Refusing to touch '$FORBIDDEN_PROJECT' - it belongs to an unrelated application. -Force does NOT override this."
  }
  try { $active = (& gcloud config get-value project 2>$null) } catch { $active = "" }
  if ($active -and $active -ne $ProjectId -and -not $Force) {
    throw "Resolved project '$ProjectId' differs from active gcloud project '$active'. Pass -Force to proceed."
  }
}

Assert-ProjectSafe

# ---- Resource plan (cost-first). gcloud strings are the real commands. ----
$svc = "monsoongrid"

Invoke-Planned "api"        "enable services"            "gcloud services enable run.googleapis.com sqladmin.googleapis.com storage.googleapis.com bigquery.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com --project $ProjectId" 0.0 "Several of these are billable; enablement is itself planned, never automatic."
Invoke-Planned "artifact"  "artifact registry repo"     "gcloud artifacts repositories create $svc --repository-format=docker --location=$Region --project $ProjectId" 0.10 "image storage"
Invoke-Planned "iam"        "run service account"        "gcloud iam service-accounts create monsoongrid-run --project $ProjectId" 0.0 "least-privilege runtime identity"
Invoke-Planned "iam"        "scheduler service account"  "gcloud iam service-accounts create monsoongrid-scheduler --project $ProjectId" 0.0 "run.invoker only"
Invoke-Planned "iam"        "build service account"      "gcloud iam service-accounts create monsoongrid-build --project $ProjectId" 0.0 "artifactregistry.writer only"
Invoke-Planned "run"        "cloud run service"          "gcloud run services replace deploy/cloudrun-service.yaml --region=$Region --project $ProjectId" 25.00 "minScale:1 idle CPU is the main variable cost"
Invoke-Planned "sql"        "cloud sql postgres 16"      "gcloud sql instances create monsoongrid-db --database-version=POSTGRES_16 --tier=db-f1-micro --region=$Region --project $ProjectId" 9.37 "ALWAYS ON - the dominant fixed cost"
Invoke-Planned "sql"        "database"                   "gcloud sql databases create monsoongrid --instance=monsoongrid-db --project $ProjectId" 0.0 ""
Invoke-Planned "storage"    "evidence bucket"            "gcloud storage buckets create gs://$ProjectId-monsoongrid-evidence --location=$Region --project $ProjectId" 0.20 "content-addressed evidence"
Invoke-Planned "storage"    "audit bucket (bucket-lock)" "gcloud storage buckets create gs://$ProjectId-monsoongrid-audit --location=$Region --retention-period=3y --project $ProjectId" 0.10 "retention-locked, append-only by platform"
Invoke-Planned "bigquery"   "metrics dataset"            "bq --project_id=$ProjectId mk --dataset --location=$Region ${ProjectId}:monsoongrid_metrics" 0.05 "3-year 15-min ledger, ~210 MB"
Invoke-Planned "secret"     "signing seed"               "gcloud secrets create monsoongrid-signing-seed --replication-policy=user-managed --locations=$Region --project $ProjectId" 0.06 "mounted as a secret volume"
Invoke-Planned "secret"     "trust fingerprint"          "gcloud secrets create monsoongrid-trust-fingerprint --project $ProjectId" 0.06 ""
Invoke-Planned "secret"     "role map"                   "gcloud secrets create monsoongrid-role-map --project $ProjectId" 0.06 ""
Invoke-Planned "secret"     "db password"                "gcloud secrets create monsoongrid-db-password --project $ProjectId" 0.06 ""
Invoke-Planned "scheduler"  "fast-loop job (PAUSED)"     "gcloud scheduler jobs create http monsoongrid-fast-loop --schedule='*/15 * * * *' --time-zone='Asia/Kolkata' --paused --project $ProjectId" 0.0 "created PAUSED; stays paused until the Postgres adapter lands"
Invoke-Planned "scheduler"  "slow-loop job (PAUSED)"     "gcloud scheduler jobs create http monsoongrid-slow-loop --schedule='15 19 * * *' --time-zone='Asia/Kolkata' --paused --project $ProjectId" 0.0 "created PAUSED"

# ---- Print the plan ----
Write-Host ""
Write-Host "=== MonsoonGrid provisioning plan (project: $ProjectId, region: $Region) ==="
if (-not $Confirm) { Write-Host "MODE: DRY-RUN (no mutation). Pass -Confirm to execute." }
$total = 0.0
foreach ($p in $script:Planned) {
  $total += [double]$p.MonthlyUsd
  "{0,-10} {1,-28} ~`${2,8:N2}/mo  {3}" -f $p.Kind, $p.Name, [double]$p.MonthlyUsd, $p.Note | Write-Host
  Write-Host ("           cmd: " + $p.Command)
}
Write-Host ("--- estimated total: ~`${0:N2}/month ---" -f $total)
Write-Host "Prices from deploy/cost/pricing-inputs.json; unverified prices are marked ASSUMPTION-UNVERIFIED there."
exit 0
