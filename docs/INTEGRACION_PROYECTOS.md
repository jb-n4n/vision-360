# Integración del paquete vision-360 en proyectos locales

Este paquete central es la fuente única de la implementación de OCR + Visión IA
360°. Los proyectos locales lo **copian (fork local)** con sus adaptaciones, nunca
lo importan como librería (los motores PaddlePaddle no son distribuibles como pip
install limpio sin el venv correspondiente).

## Patrón general (3 pasos)

1. **Copiar los módulos** del paquete al proyecto destino (subdirectorio `scripts/ocr/`
   o similar). Respetar la licencia CC BY-SA 4.0: attribution en el header de cada
   archivo adaptado (`Adaptado de better-ocr...`).
2. **Imports robustos** si el módulo vive en un subdirectorio: try/except con
   doble ruta (package vs directo). Patrón probado:

   ```python
   import os, sys
   try:
       from scripts.ocr.ocr_rapido import emparejar, extraer_tabla
   except ImportError:  # ejecución directa: python scripts/ocr/ocr_rapido.py
       sys.path.insert(0, os.path.dirname(__file__))
       from ocr_rapido import emparejar, extraer_tabla
   ```

3. **Venv dedicado** (`.venv-ocr`) creado con `setup-ocr.ps1`; los daemons
   (`ocr_server.py`, `chart_server.py`) se lanzan una vez y se matan al terminar
   la suite de pruebas — nunca un intérprete Python por captura.

## En drilling-visualization (React + Vite + Tauri) — ya integrado

- Ubicación: `scripts/ocr/` con `tests/` propios y `setup-ocr.ps1` (instala el
  venv para los e2e).
- `package.json`:
  - `test:ocr`: `playwright test visual-ocr.spec.js`
  - `test:ocr:setup`: `powershell -ExecutionPolicy Bypass -File scripts/ocr/setup-ocr.ps1`
- `e2e/visual-ocr.spec.js` lanza `ocr_server.py` en `beforeAll` y lo mata en
  `afterAll`; los asserts de texto usan `ocr_verify.py` (PP-OCRv6, lang=es).
- La suite e2e **se auto-salta** si falta el venv (`spawnSync` de
  `.venv-ocr/Scripts/python.exe`).
- Vision 360: `scripts/ocr/vision360.py --image shot.png --regions regions.json
  --engine ollama` contra el daemon (`--daemon-url http://127.0.0.1:8131`).
- Reglas: ver `AGENTS.md` del proyecto (sección "OCR & AI Vision Testing").

## En multistat (PySide6, escritorio) — integración parcial

- Ya tiene OCR para auditorías de UI (RapidOCR/EasyOCR lazy singleton, ver
  `tests/integration/test_demo_auto.py::_ocr_reader`) y la descripción 360 de
  paneles Vulcan (`tools/comparacion_antecedente/descripcion_360.py`: EasyOCR +
  OpenCV + 3 VLM de Ollama).
- Para subir al stack completo de este paquete:
  1. Copiar `ocr_server.py`, `ocr_verify.py`, `vision360.py`, `vision.py`,
     `ocr_rapido.py`, `chart_ocr.py`, `chart_server.py` a un subdirectorio
     (p. ej. `tools/vision360/`) con el patrón de imports robustos.
  2. Crear `.venv-ocr` con `setup-ocr.ps1` (no usar el `.venv` de la app: los
     motores VLM son pesados y no thread-safe).
  3. Sustituir el harness de `descripcion_360.py` por `vision360.py` cuando se
     necesite verdad de campo (DOM/regiones), no solo descripción libre.
- Reglas: ver `AGENTS.md` del proyecto destino (los permisos, nombres y estilos
  del destino mandan; P1.16).

## En cualquier otro proyecto

- Copiar los módulos necesarios según la necesidad:
  - Solo texto en pantalla: `ocr_verify.py` (o daemon `ocr_server.py`).
  - Contar/verificar UIs densas: `vision360.py` + daemon (SoM + DOM).
  - Tablas de gráficos: `chart_ocr.py` o cascada `ocr_rapido.py` + fallback.
  - Visión multi-modo: `vision.py` (CLI) o `chart_server.py` POST /vision.
- Copiar también `docs/GUIA_OCR_VISION.md` (documento general reutilizable) y
  el `AGENTS.md` si el destino no tiene reglas propias (adaptando la sección
  "Reglas específicas" al destino).
- Licencia: mantener la atribución CC BY-SA 4.0 en los headers y en el README
  del destino.

## Instancia única de Ollama compartida

Los proyectos con Visión IA 360 (vision-360, drilling-visualization, multistat)
comparten **una sola instancia de Ollama** en `127.0.0.1:11434` — nunca una por
proyecto. Ya es así por construcción:

| Proyecto | Endpoint | Uso |
|---|---|---|
| vision-360 (`ocr_server.py`, `ocr_verify.py`) | `http://127.0.0.1:11434/api/chat` | motores `ollama`/`gemma3` |
| drilling-visualization (idéntico) | `http://127.0.0.1:11434/api/chat` | QA de capturas e2e |
| multistat (`descripcion_360.py`) | `http://localhost:11434/api/generate` | descripción 360 de paneles |

Configuración recomendada (host 16 GB) y cómo aplicarla: ver `README.md`
sección "Instancia única de Ollama compartida". Script idempotente:
`scripts/ollama_compartida.ps1` (`-Check` estado / `-Apply` aplica + reinicia +
limpia huérfanos `llama-server`).

## Checklist de integración

- [ ] Imports robustos (package vs directo) probados en ambas rutas
- [ ] Tests del paquete pasan en el destino (`-m unittest discover -s tests`)
- [ ] `.venv-ocr` creado con `setup-ocr.ps1`; `%TMP%` con > 3 GB libres
- [ ] Daemons: una sola instancia, auto-cierre por inactividad, kill en teardown
- [ ] `lang="es"` por defecto, nunca hardcodeado (es/pt/en)
- [ ] Atribución CC BY-SA 4.0 presente; reglas del destino respetadas (P1.16)
