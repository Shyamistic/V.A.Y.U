param([int]$Seed = 20260701, [ValidateSet('reliability_first','balanced','cost_first')][string]$Policy = 'balanced', [double]$Deadline = 285, [string]$RuntimeRoot = '')
$ErrorActionPreference = 'Stop'
$product = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $product
$python = Join-Path $workspace '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $product)) { throw "Product root is missing" }
if (-not (Test-Path -LiteralPath (Join-Path $product 'src\monsoon_grid'))) { throw "Source package is missing" }
if (-not (Test-Path -LiteralPath $python)) { throw "Supplied executor is missing" }
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { $RuntimeRoot = Join-Path $product 'runtime' }
$env:MONSOON_GRID_RUNTIME_ROOT = $RuntimeRoot
$env:PYTHONPATH = "$(Join-Path $product 'src');$(Join-Path $product 'runtime\site-packages')"
$trust = Join-Path $product 'trust'
$env:MONSOON_GRID_TRUST_KEYRING = Join-Path $trust 'verification-keyring.json'
$env:MONSOON_GRID_SIGNING_KEY = Join-Path $trust 'demo-signing-seed.hex'
$env:MONSOON_GRID_TRUST_FINGERPRINT = (Get-Content -Raw -LiteralPath (Join-Path $trust 'verification-fingerprint.txt')).Trim()
& $python -m monsoon_grid replay --seed $Seed --policy $Policy --deadline $Deadline
exit $LASTEXITCODE
