#!/usr/bin/env bash
# Verificación de coherencia del proyecto better-ocr (patrón better-ai: sin
# dependencia de GitHub/CI; verificación local previa a cada commit).
# Uso: bash scripts/verificar-proyecto.sh [--pre-commit]
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  [OK] $desc"
        PASS=$((PASS + 1))
    else
        echo "  [FALLO] $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "== 1. Reglas =="
check "12 reglas P0 definidas en AGENTS.md" bash -c "test \$(grep -cE '^### P0' AGENTS.md) -eq 12"
check "18 reglas P1 definidas en AGENTS.md" bash -c "test \$(grep -cE '^### P1' AGENTS.md) -eq 18"
check "referencias a rutas docs/ scripts/ y ejemplos/ existen" python3 -c "
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
    for m in re.findall(r'(?:docs/|scripts/|ejemplos/)[A-Za-z0-9_./-]+', open(f).read()):
        if m in externos:
            continue
        rutas.add(m.rstrip('/'))
faltan = [r for r in sorted(rutas) if not os.path.exists(r)]
assert not faltan, 'referencias rotas: ' + str(faltan)
"
check "ningun .env versionado en git" bash -c "test -z \"\$(git ls-files | grep -E '\\.env(\$|\\.)' | grep -v '\\.env\\.example')\""
check "sin dependencia de GitHub Actions (.github eliminado)" bash -c "test ! -d .github"
check "IDs citados en CHECKLIST y README existen en AGENTS.md" bash -c "test -z \"\$(comm -23 <(grep -ohE 'P[0-2]\\.[0-9]+' CHECKLIST.md README.md | sort -u) <(grep -ohE 'P[0-2]\\.[0-9]+' AGENTS.md | sort -u))\""
check "sin referencias obsoletas en AGENTS.md/README.md (master, GitHub Actions, better-ia)" bash -c "! grep -E '(GitHub Actions|branches: \\[master\\]|push a \`master\`|better-ia|\\.github/workflows)' AGENTS.md README.md"

echo "== 2. Sintaxis y pruebas =="
check "sintaxis: extractor_final.py" python3 -m py_compile extractor_final.py
check "sintaxis: chart_server.py" python3 -m py_compile chart_server.py
check "sintaxis: ocr_rapido.py" python3 -m py_compile ocr_rapido.py
check "sintaxis: vision.py" python3 -m py_compile vision.py
check "tests unitarios (stdlib + pandas)" python3 -m unittest discover -s tests -q

echo "== 3. Config =="
check "opencode.json es JSON valido" python3 -c "import json; json.load(open('opencode.json'))"
check "deny criticos en opencode.json (rm -rf, reset --hard, .env)" python3 -c "
import json
p = json.load(open('opencode.json'))['permission']
assert p['bash'].get('rm -rf *') == 'deny'
assert p['bash'].get('git reset --hard*') == 'deny'
assert p['edit'].get('*.env') == 'deny'
assert p['read'].get('*.env') == 'deny'
"

echo "== 4. Seguridad (P0.9/P0.10) =="
check "sin IPs, claves o rutas .ssh en archivos" bash -c "! grep -rnE '(id_rsa|id_ed25519|\\.ssh/|known_hosts|([0-9]{1,3}\\.){3}[0-9]{1,3})' --include='*.md' --include='*.json' --include='*.sh' --include='.gitignore' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules . | grep -qvE '(deny|patrones|claves SSH|no leas|comitees|127\\.0\\.0\\.1)'"
check "sin emails personales en archivos" bash -c "! grep -rnE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}' --include='*.md' --include='*.json' --include='*.sh' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules . | grep -qvE '(youremail@example|creativecommons|github)'"

echo "== 5. Repositorio =="
check "hook pre-commit instalado identico al script" bash -c "cmp -s scripts/hooks/pre-commit .git/hooks/pre-commit"
if [ "${1:-}" = "--pre-commit" ]; then
    echo "  [SKIP] comprobaciones de repositorio (modo pre-commit: los archivos staged son el cambio)"
else
    check "arbol de trabajo limpio" bash -c "test -z \"\$(git status --porcelain)\""
    check "rama main sincronizada con origin" bash -c "test -z \"\$(git status --porcelain --branch | grep -E 'adelant|ahead|behind|adelanta')\""
fi

echo
echo "Resultado: $PASS OK, $FAIL FALLOS"
[ "$FAIL" -eq 0 ] || exit 1
