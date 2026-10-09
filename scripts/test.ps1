param([string]$PytestArgs = '')
$ErrorActionPreference = 'Stop'
$product = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $product
$python = Join-Path $workspace '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $product)) { throw "Product root is missing" }
if (-not (Test-Path -LiteralPath (Join-Path $product 'src\monsoon_grid'))) { throw "Source package is missing" }
if (-not (Test-Path -LiteralPath $python)) { throw "Supplied executor is missing" }
$env:PYTHONPATH = "$(Join-Path $product 'src');$(Join-Path $product 'runtime\site-packages')"
if ([string]::IsNullOrWhiteSpace($PytestArgs)) {
  & $python -m pytest -q (Join-Path $product 'tests')
} else {
  & $python -m pytest -q $PytestArgs
}
exit $LASTEXITCODE
