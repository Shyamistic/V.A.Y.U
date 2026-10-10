<#
.SYNOPSIS
  Local container build + C2 verification harness (design §9.4, AC-32..36).

  Runs the checks in order and writes a result block for docs/deployment.md.
  If the Docker daemon is unavailable it exits 2 with DOCKER_DAEMON_UNAVAILABLE
  and invents NO measurement. No step result is ever asserted from reasoning.

  This script BUILDS and RUNS locally only. It never pushes an image.
#>
[CmdletBinding()]
param(
  [string]$Tag = "monsoongrid:local",
  [int]$HostPort = 18080
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$results = [ordered]@{}

function Write-Result {
  param([string]$Key, $Value)
  $script:results[$Key] = $Value
  Write-Host ("{0}: {1}" -f $Key, $Value)
}

# 1. Preflight: is the Docker daemon reachable?
try {
  $server = & docker version --format '{{.Server.Version}}' 2>&1
  if ($LASTEXITCODE -ne 0 -or -not $server) {
    Write-Result "status" "DOCKER_DAEMON_UNAVAILABLE"
    Write-Result "detail" ("docker CLI present but the engine is not reachable: " + ($server -join ' '))
    Write-Result "remediation" "Start Docker Desktop and wait for the Linux engine, then re-run deploy/verify_image.ps1."
    Write-Host "DOCKER_DAEMON_UNAVAILABLE"
    exit 2
  }
  Write-Result "docker_server_version" ($server -join ' ')
} catch {
  Write-Result "status" "DOCKER_DAEMON_UNAVAILABLE"
  Write-Result "detail" $_.Exception.Message
  Write-Result "remediation" "Start Docker Desktop and wait for the Linux engine, then re-run deploy/verify_image.ps1."
  Write-Host "DOCKER_DAEMON_UNAVAILABLE"
  exit 2
}

Push-Location $repoRoot
try {
  # resolve source revision + digest from the python resolver
  $rev = (& python -c "import pathlib,sys; sys.path.insert(0,'.'); from deploy.source_revision import resolve_source_revision; r,d=resolve_source_revision(pathlib.Path('.')); print(r)")
  $digest = (& python -c "import pathlib,sys; sys.path.insert(0,'.'); from deploy.source_revision import resolve_source_revision; r,d=resolve_source_revision(pathlib.Path('.')); print(d)")
  Write-Result "source_revision" $rev

  # 2. build with plain progress (capture transferred context size)
  $buildLog = & docker build -f deploy/Dockerfile -t $Tag `
    --build-arg "SOURCE_REVISION=$rev" --build-arg "SOURCE_TREE_DIGEST=$digest" `
    --progress=plain . 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Result "status" "BUILD_FAILED"
    Write-Result "build_tail" (($buildLog | Select-Object -Last 20) -join "`n")
    exit 1
  }
  $contextLine = $buildLog | Select-String "transferring context" | Select-Object -Last 1
  if ($contextLine) { Write-Result "transferred_context" $contextLine.ToString().Trim() }

  # 3. real image size
  $size = & docker image inspect $Tag --format '{{.Size}}'
  Write-Result "image_size_bytes" $size
  Write-Result "image_size_mb" ([math]::Round([double]$size / 1MB, 1))

  # 4. run under --read-only with a writable runtime mount, production auth mode
  & docker rm -f monsoongrid-verify 2>$null | Out-Null
  & docker run -d --rm --name monsoongrid-verify -p "$($HostPort):8080" `
    --read-only --tmpfs /tmp --mount "type=volume,dst=/var/monsoon/runtime" `
    -e MONSOON_GRID_ENV=production -e MONSOON_GRID_AUTH_MODE=production `
    -e MONSOON_GRID_AUTH_AUDIENCE=https://verify.local `
    -e MONSOON_GRID_ROLE_MAP='{"schema_version":"1.0","by_subject":{}}' `
    -e MONSOON_GRID_TRUST_FINGERPRINT=placeholder `
    -e MONSOON_GRID_SIGNING_KEY=/opt/monsoon/trust/demo-signing-seed.hex `
    $Tag | Out-Null

  # measured startup time: docker run return -> first 200 on /_ops/healthz
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $healthy = $false
  while ($sw.Elapsed.TotalSeconds -lt 60) {
    try {
      $r = Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort/_ops/healthz" -TimeoutSec 2 -UseBasicParsing
      if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { Start-Sleep -Milliseconds 200 }
  }
  $sw.Stop()
  if (-not $healthy) {
    Write-Result "status" "HEALTH_TIMEOUT"
    Write-Result "container_logs" ((& docker logs monsoongrid-verify 2>&1 | Select-Object -Last 30) -join "`n")
    & docker stop monsoongrid-verify | Out-Null
    exit 1
  }
  Write-Result "startup_seconds" ([math]::Round($sw.Elapsed.TotalSeconds, 2))

  # 5. HiGHS solve inside the container
  $milp = & docker exec monsoongrid-verify python -c "import numpy as np; from scipy.optimize import milp, LinearConstraint, Bounds; r=milp(c=np.array([1.0,1.0]), integrality=np.array([1,1]), bounds=Bounds(np.array([0.0,0.0]), np.array([5.0,5.0])), constraints=LinearConstraint(np.array([[1.0,1.0]]),2.0,2.0)); print('milp', r.success, r.status)"
  Write-Result "highs_solve" ($milp -join ' ')

  # 6. unauthenticated mutation rejection on all eleven mutating routes
  $mutating = @('/replays','/replays/x/advance','/validate','/planning','/approvals','/demo',
                '/api/deliberate','/api/multimodal/document','/api/multimodal/nowcast',
                '/_sched/fast-loop','/_sched/slow-loop')
  $all401 = $true
  foreach ($route in $mutating) {
    try {
      Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort$route" -Method POST -Body '{}' -ContentType 'application/json' -TimeoutSec 5 -UseBasicParsing | Out-Null
      $all401 = $false
    } catch {
      if ($_.Exception.Response.StatusCode.value__ -ne 401) { $all401 = $false }
    }
  }
  Write-Result "all_mutating_401_without_credentials" $all401

  # health still 200 in the same run
  $h = Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort/_ops/healthz" -TimeoutSec 5 -UseBasicParsing
  Write-Result "healthz_200" ($h.StatusCode -eq 200)

  # 7. readyz under read-only
  try {
    $ready = Invoke-WebRequest -Uri "http://127.0.0.1:$HostPort/_ops/readyz" -TimeoutSec 5 -UseBasicParsing
    Write-Result "readyz_status" $ready.StatusCode
  } catch {
    Write-Result "readyz_status" $_.Exception.Response.StatusCode.value__
  }

  & docker stop monsoongrid-verify | Out-Null
  Write-Result "status" "VERIFIED"
  $script:results | ConvertTo-Json -Depth 4
  exit 0
} finally {
  Pop-Location
}
