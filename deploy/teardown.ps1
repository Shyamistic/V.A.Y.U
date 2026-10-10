<#
.SYNOPSIS
  Dry-run-default Google Cloud teardown plan (design §10.4, FR-C5, AC-38).

  Same safety skeleton as provision.ps1: dry-run default, single Invoke-Planned
  gate, project guard that unconditionally refuses fablecraft-4ab6c. Additionally
  requires -IUnderstandDataLoss for Cloud SQL and for any retention-locked bucket,
  and prints what would be PERMANENTLY lost before the total.

  The audit bucket is retention-locked, so its deletion FAILS at the platform
  layer by design; the plan prints that expected outcome so it is not read as a
  script bug.
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "",
  [string]$Region = "asia-south1",
  [switch]$Confirm,
  [switch]$Force,
  [switch]$IUnderstandDataLoss
)
$ErrorActionPreference = "Stop"
$script:Planned = @()
$FORBIDDEN_PROJECT = "fablecraft-4ab6c"

function Add-Planned { param($Kind,$Name,$Command,$Note,[bool]$DataLoss=$false)
  $script:Planned += [pscustomobject]@{ Kind=$Kind; Name=$Name; Command=$Command; Note=$Note; DataLoss=$DataLoss } }

function Invoke-Planned {
  param($Kind,$Name,$Command,$Note="",[bool]$DataLoss=$false)
  Add-Planned -Kind $Kind -Name $Name -Command $Command -Note $Note -DataLoss $DataLoss
  if (-not $Confirm) { return }
  if ($DataLoss -and -not $IUnderstandDataLoss) {
    Write-Host "SKIP (data loss, no -IUnderstandDataLoss): $Name"; return
  }
  Write-Host "EXECUTING: $Command"
  Invoke-Expression $Command
}

function Assert-ProjectSafe {
  if ([string]::IsNullOrWhiteSpace($ProjectId)) { throw "ProjectId is required." }
  if ($ProjectId -eq $FORBIDDEN_PROJECT) {
    throw "Refusing to touch '$FORBIDDEN_PROJECT' - unrelated application. -Force does NOT override this."
  }
  try { $active = (& gcloud config get-value project 2>$null) } catch { $active = "" }
  if ($active -and $active -ne $ProjectId -and -not $Force) {
    throw "Resolved project '$ProjectId' differs from active '$active'. Pass -Force to proceed."
  }
}

Assert-ProjectSafe

Invoke-Planned "scheduler" "delete fast-loop" "gcloud scheduler jobs delete monsoongrid-fast-loop --project $ProjectId --quiet"
Invoke-Planned "scheduler" "delete slow-loop" "gcloud scheduler jobs delete monsoongrid-slow-loop --project $ProjectId --quiet"
Invoke-Planned "run"       "delete service"   "gcloud run services delete monsoongrid --region=$Region --project $ProjectId --quiet"
Invoke-Planned "bigquery"  "delete dataset"   "bq --project_id=$ProjectId rm -r -f -d ${ProjectId}:monsoongrid_metrics" "destroys the 3-year metrics ledger" $true
Invoke-Planned "sql"       "delete instance"  "gcloud sql instances delete monsoongrid-db --project $ProjectId --quiet" "destroys all durable certificate/approval/event state" $true
Invoke-Planned "storage"   "delete evidence"  "gcloud storage rm --recursive gs://$ProjectId-monsoongrid-evidence" "destroys content-addressed evidence objects" $true
Invoke-Planned "storage"   "delete audit"     "gcloud storage rm --recursive gs://$ProjectId-monsoongrid-audit" "EXPECTED TO FAIL: bucket-lock retention prevents deletion by design" $true
Invoke-Planned "secret"    "delete secrets"   "gcloud secrets delete monsoongrid-signing-seed monsoongrid-trust-fingerprint monsoongrid-role-map monsoongrid-db-password --project $ProjectId --quiet" "removes signing material" $true
Invoke-Planned "artifact"  "delete repo"      "gcloud artifacts repositories delete monsoongrid --location=$Region --project $ProjectId --quiet"

Write-Host ""
Write-Host "=== MonsoonGrid teardown plan (project: $ProjectId, region: $Region) ==="
if (-not $Confirm) { Write-Host "MODE: DRY-RUN (no mutation). Pass -Confirm (and -IUnderstandDataLoss for destructive items) to execute." }
Write-Host "PERMANENT DATA LOSS on the items marked below:"
foreach ($p in $script:Planned) {
  $flag = if ($p.DataLoss) { "[DATA LOSS]" } else { "           " }
  "{0} {1,-10} {2,-18} {3}" -f $flag, $p.Kind, $p.Name, $p.Note | Write-Host
  Write-Host ("            cmd: " + $p.Command)
}
exit 0
