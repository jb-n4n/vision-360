# Verificación de coherencia del paquete central vision-360 (port Windows de
# scripts/verificar-proyecto.sh de better-ocr, CC BY-SA 4.0).
# Uso:  powershell -ExecutionPolicy Bypass -File scripts/verificar-proyecto.ps1 [--pre-commit]
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')

$pass = 0
$fail = 0

function Check {
    param([string]$desc, [scriptblock]$body)
    try {
        & $body | Out-Null
        Write-Output "  [OK] $desc"
        $script:pass++
    } catch {
        Write-Output "  [FALLO] $desc"
        Write-Output "        $($_.Exception.Message)"
        $script:fail++
    }
}

function Get-Python {
    $candidates = @(
        ".venv-ocr\Scripts\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'No se encontro Python (se esperaba .venv-ocr o un Python 3.11-3.13)'
}

$py = Get-Python
Write-Output "== 1. Reglas =="
Check "12 reglas P0 definidas en AGENTS.md" {
    $n = (Select-String -Path AGENTS.md -Pattern '^### P0' -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($n -ne 12) { throw "P0: $n (esperado 12)" }
}
Check "18 reglas P1 definidas en AGENTS.md" {
    $n = (Select-String -Path AGENTS.md -Pattern '^### P1' -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($n -ne 18) { throw "P1: $n (esperado 18)" }
}
Check "referencias a rutas docs/ scripts/ y ejemplos/ existen" {
    & $py -c @"
import re, os
files = ['AGENTS.md', 'README.md', 'CHECKLIST.md', 'docs/GUIA_OCR_VISION.md', 'docs/LECCIONES-APRENDIDAS.md']
externos = {
    'docs/REGLAS-COMPLETAS.md', 'docs/PRUEBAS.md',
    'docs/version3.x/installation.md',
    'docs/version3.x/pipeline_usage/OCR.en.md',
    'docs/version3.x/pipeline_usage/doc_understanding.md',
    'docs/version3.x/module_usage',
}
rutas = set()
for f in files:
    if not os.path.exists(f):
        continue
    for m in re.findall(r'(?:docs/|scripts/|ejemplos/)[A-Za-z0-9_./-]+', open(f, encoding='utf-8').read()):
        if m in externos:
            continue
        rutas.add(m.rstrip('/'))
faltan = [r for r in sorted(rutas) if not os.path.exists(r)]
assert not faltan, 'referencias rotas: ' + str(faltan)
"@ | Out-Null
}
Check "sin dependencia de GitHub Actions (.github eliminado)" {
    if (Test-Path .github) { throw '.github existe' }
}
Check "IDs citados en CHECKLIST y README existen en AGENTS.md" {
    $ids = (Select-String -Path CHECKLIST.md, README.md -Pattern 'P[0-2]\.[0-9]+' -AllMatches |
        ForEach-Object { $_.Matches.Value }) | Sort-Object -Unique
    $defs = (Select-String -Path AGENTS.md -Pattern 'P[0-2]\.[0-9]+' -AllMatches |
        ForEach-Object { $_.Matches.Value }) | Sort-Object -Unique
    $faltan = $ids | Where-Object { $_ -notin $defs }
    if ($faltan) { throw "IDs sin definir: $($faltan -join ', ')" }
}
Check "sin referencias obsoletas en AGENTS.md/README.md (master, GitHub Actions, better-ia)" {
    $m = Select-String -Path AGENTS.md, README.md -Pattern 'GitHub Actions|branches: \[master\]|push a `master`|better-ia|\.github/workflows' -ErrorAction SilentlyContinue
    if ($m) { throw "referencias obsoletas: $($m.Line -join '; ')" }
}

Write-Output "== 2. Sintaxis y pruebas =="
foreach ($f in @('extractor_final.py','chart_server.py','chart_ocr.py','ocr_rapido.py','ocr_server.py','ocr_verify.py','vision.py','vision360.py')) {
    Check "sintaxis: $f" { & $py -m py_compile $f | Out-Null }
}
Check "tests unitarios (stdlib + pandas + pillow)" {
    & $py -m unittest discover -s tests -q | Out-Null
}

Write-Output "== 3. Config =="
Check "opencode.json es JSON valido" {
    Get-Content opencode.json -Raw | ConvertFrom-Json | Out-Null
}
Check "deny criticos en opencode.json (rm -rf, reset --hard, .env)" {
    $p = (Get-Content opencode.json -Raw | ConvertFrom-Json).permission
    if ($p.bash.'rm -rf *' -ne 'deny') { throw 'rm -rf * no es deny' }
    if ($p.bash.'git reset --hard*' -ne 'deny') { throw 'git reset --hard* no es deny' }
    if ($p.edit.'*.env' -ne 'deny') { throw 'edit *.env no es deny' }
    if ($p.read.'*.env' -ne 'deny') { throw 'read *.env no es deny' }
}

Write-Output "== 4. Seguridad (P0.9/P0.10) =="
Check "sin IPs, claves o rutas .ssh en archivos" {
    $m = Get-ChildItem -Recurse -File -Include *.md, *.json, *.ps1, *.py, .gitignore |
        Where-Object { $_.FullName -notmatch '\\\.git\\|\\\.venv' } |
        Select-String -Pattern 'id_rsa|id_ed25519|\.ssh/|known_hosts|(\d{1,3}\.){3}\d{1,3}' -ErrorAction SilentlyContinue |
        Where-Object { $_.Line -notmatch 'deny|patrones|claves SSH|no leas|comitees|127\.0\.0\.1|id_rsa|known_hosts' }
    if ($m) { throw "hallazgos: $(($m | ForEach-Object { $_.Path }) -join '; ')" }
}
Check "sin emails personales en archivos" {
    $m = Get-ChildItem -Recurse -File -Include *.md, *.json, *.ps1, *.py |
        Where-Object { $_.FullName -notmatch '\\\.git\\|\\\.venv' } |
        Select-String -Pattern '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -ErrorAction SilentlyContinue |
        Where-Object { $_.Line -notmatch 'youremail@example|creativecommons|github' }
    if ($m) { throw "hallazgos: $($m.Path -join '; ')" }
}

Write-Output "== 5. Repositorio =="
$isRepo = Test-Path .git
if ($args -contains '--pre-commit') {
    Write-Output "  [SKIP] comprobaciones de repositorio (modo pre-commit: los archivos staged son el cambio)"
} elseif (-not $isRepo) {
    Write-Output "  [SKIP] no es un repo git todavia (git init para activar las comprobaciones)"
} else {
    Check "arbol de trabajo limpio" {
        if ((git status --porcelain) -ne '') { throw 'hay cambios sin commitear' }
    }
    Check "rama main sincronizada con origin" {
        $b = git status --porcelain --branch
        if ($b -match 'ahead|behind') { throw 'rama desincronizada' }
    }
}

Write-Output ""
Write-Output "Resultado: $pass OK, $fail FALLOS"
if ($fail -ne 0) { exit 1 }
