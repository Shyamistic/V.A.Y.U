param(
  [string]$Output = "",
  [string]$CompareTo = ""
)
$ErrorActionPreference = 'Stop'
$product = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $product
$python = Join-Path $workspace '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $product)) { throw "MonsoonGrid product root is missing: $product" }
if (-not (Test-Path -LiteralPath (Join-Path $product 'src\monsoon_grid'))) { throw "MonsoonGrid source package is missing" }
if (-not (Test-Path -LiteralPath $python)) { throw "Supplied executor is missing: $python" }
if ([string]::IsNullOrWhiteSpace($Output)) { $Output = Join-Path $product 'runtime\provenance\protected_manifest_before.json' }
$env:PYTHONPATH = "$(Join-Path $product 'src');$(Join-Path $product 'runtime\site-packages')"
$args = @('-m','monsoon_grid','inventory','--output',$Output)
if (-not [string]::IsNullOrWhiteSpace($CompareTo)) { $args += @('--compare-to',$CompareTo) }
& $python @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
