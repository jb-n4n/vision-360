# Sets up the local PaddleOCR venv used by e2e/visual-ocr.spec.js.
# Run from the repository root. Python 3.11/3.12/3.13 is required
# (3.14 is not yet supported by PaddlePaddle wheels).
#
#   powershell -ExecutionPolicy Bypass -File scripts/ocr/setup-ocr.ps1
#
# Installs the full PaddleOCR stack (better-ocr guide, July 2026):
#   - Text OCR:   PaddleOCR + PP-OCRv6 (light, ~133 MB models)
#   - AI Vision:  DocUnderstanding (PP-DocBee) via the [doc-parser] extra
#   - Chart OCR:  ChartParsing (PP-Chart2Table) via the [doc-parser] extra
#
# Version pins from the upstream better-ocr requirements.txt (July 2026):
# paddlepaddle==3.3.1 + paddleocr[doc-parser]==3.7.0. On Linux the CPU wheels
# come from https://www.paddlepaddle.org.cn/packages/stable/cpu/; on Windows
# PyPI ships CPU wheels. PaddlePaddle 3.3.1 has a PIR + oneDNN bug
# (PaddlePaddle/PaddleOCR#18162): the scripts disable mkldnn explicitly
# (enable_mkldnn=False / PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0).
#
# Note (OSError 122): model downloads buffer through the temp directory.
# On Windows, ensure %TMP%/%TEMP% points to a drive with > 3 GB free.

$ErrorActionPreference = 'Stop'

function Find-Python {
  $candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $v = & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info[:2] in [(3,11),(3,12),(3,13)] else 1)"
    if ($LASTEXITCODE -eq 0) { return $cmd.Source }
  }
  throw 'No Python 3.11-3.13 found. Install one first.'
}

$py = Find-Python
Write-Host "Using Python: $py"
if (-not (Test-Path .venv-ocr)) {
  & $py -m venv .venv-ocr
}
& .venv-ocr\Scripts\python.exe -m pip install --upgrade pip
& .venv-ocr\Scripts\python.exe -m pip install "paddlepaddle==3.3.1" "paddleocr[doc-parser]==3.7.0"

Write-Host ''
Write-Host 'OCR venv ready. Run: npx playwright test visual-ocr.spec.js'
Write-Host 'VLM models (PP-DocBee, PP-Chart2Table) download on first use (~4-8 GB).'
