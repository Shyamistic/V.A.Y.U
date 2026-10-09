param([string]$CertificatePath = '', [string]$RuntimeRoot = '')
$ErrorActionPreference = 'Stop'
$product = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $product
$python = Join-Path $workspace '.venv\Scripts\python.exe'
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { $RuntimeRoot = Join-Path $product 'runtime' }
$env:MONSOON_GRID_RUNTIME_ROOT = $RuntimeRoot
if ([string]::IsNullOrWhiteSpace($CertificatePath)) { $CertificatePath = Join-Path $RuntimeRoot 'artifacts\certificate.json' }
if (-not (Test-Path -LiteralPath $product)) { throw "Product root is missing" }
if (-not (Test-Path -LiteralPath $CertificatePath)) { throw "Certificate is missing: $CertificatePath" }
if (-not (Test-Path -LiteralPath $python)) { throw "Supplied executor is missing" }
$env:PYTHONPATH = "$(Join-Path $product 'src');$(Join-Path $product 'runtime\site-packages')"
$trust = Join-Path $product 'trust'
$env:MONSOON_GRID_TRUST_KEYRING = Join-Path $trust 'verification-keyring.json'
$env:MONSOON_GRID_TRUST_FINGERPRINT = (Get-Content -Raw -LiteralPath (Join-Path $trust 'verification-fingerprint.txt')).Trim()
& $python -m monsoon_grid verify-certificate --certificate-path $CertificatePath
exit $LASTEXITCODE
