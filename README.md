# Vision IA 360 + better-ocr — paquete central para proyectos locales

## Objetivo del proyecto

Paquete central y reutilizable de **visión IA 360° y OCR** (mejor-ocr), listo para
copiar/instalar en proyectos locales (web/Tauri, escritorio Python, etc.) que
necesiten leer pantallas, gráficos, documentos y objetos:

- **Text OCR:** PP-OCRv6 (rápido, ~133 MB de modelos) con `ocr_verify.py` y el
  daemon persistente `ocr_server.py`.
- **AI Vision (preguntas en lenguaje natural):** PP-DocBee (`docbee`), PaddleOCR-VL
  (`paddleocr-vl`) y motores Ollama locales (`ollama` = qwen2.5vl:3b, `gemma3` =
  gemma3:4b) vía `ocr_server.py` (POST /ask).
- **Vision 360 híbrida:** `vision360.py` = Set-of-Marks + verdad de campo DOM +
  QA VLM por regiones (el conteo con VLM puro falla en UIs densas: 5/6 paneles;
  con SoM cuenta 6/6).
- **Chart OCR:** `chart_ocr.py` (PP-Chart2Table, ~2.2 GB) y cascada rápida
  `ocr_rapido.py` (PP-OCRv6 + emparejamiento geométrico + gate de plausibilidad;
  si el gate falla, el llamador cae al VLM ChartParsing).
- **CLI multi-modo:** `vision.py` — modos `auto/texto/graficos/doc/objetos/humano`
  con perfiles de RAM por máquina (`BETTER_OCR_PERFIL` / `better_ocr.json`).
- **Daemons persistentes:** `chart_server.py` (POST /chart) y `ocr_server.py`
  (POST /ocr, POST /ask) — modelos cargados UNA vez, auto-cierre tras 1 h sin
  peticiones, PaddleX serializado (no thread-safe, una instancia VLM por máquina).

**Estado de verificación:** heredado del upstream better-ocr (verificado en Arch y
Kubuntu: 6/6 valores exactos con ChartParsing; PP-OCRv6 12/12 en ~56 s; RT-DETR-L
~18 s; PP-StructureV3 layout ~323 s) + adaptaciones Windows probadas en el
proyecto drilling-visualization (daemon ocr_server, vision360 SoM, setup-ocr.ps1).
Los tests unitarios de este paquete se ejecutan sin paddleocr (modelos simulados).

## Origen y licencia

Este paquete es un **fork adaptado a Windows** de
[jmbigi/better-ocr](https://github.com/jmbigi/better-ocr) (CC BY-SA 4.0), con
aportes del proyecto **drilling-visualization** (Nex4Nova, código propietario del
que solo se incluyen las adaptaciones OCR aquí contenidas).

- Upstream: <https://github.com/jmbigi/better-ocr> y <https://codeberg.org/jmbigi/better-ocr>
- Reglas de IA: [jmbigi/better-ai](https://github.com/jmbigi/better-ai) (CC BY-SA 4.0)
- Licencia de este paquete: **CC BY-SA 4.0** (ver `LICENSE`). Si lo copias a un
  proyecto comercial, respeta los términos share-alike de la parte copiada.

## Archivos del paquete

| Archivo | Descripción |
| :--- | :--- |
| `AGENTS.md` | **Reglas de IA del proyecto** (conjunto better-ai, CC BY-SA 4.0, con reglas específicas de este paquete). opencode lo carga automáticamente. |
| `opencode.json` | **Guardarraíles deterministas** para opencode: `deny` de comandos destructivos y edición/lectura de `.env`. |
| `CHECKLIST.md` | Checklist de verificación pre-entrega (imprimible). |
| `README.md` | Este archivo: identidad del paquete, uso y referencias. |
| `vision.py` | **CLI multi-modo de visión:** `auto` (clasifica y rutea), `texto`, `graficos` (cascada), `doc` (layout), `objetos`/`humano` (RT-DETR-L). Salidas json/csv/md. |
| `ocr_rapido.py` | **Ruta rápida en cascada:** PP-OCRv6 + emparejamiento geométrico por bboxes (año↔valor) con gate de plausibilidad. Fallo del gate = usar ChartParsing. |
| `extractor_final.py` | Extracción directa con ChartParsing: `datos_extraidos.csv` + `salida_bruta.json`. Expone `obtener_markdown()`, `markdown_a_df()`, `validar_imagen()`. |
| `chart_ocr.py` | CLI Chart OCR (PP-Chart2Table): `python chart_ocr.py imagen.png [--json] [--csv out.csv] [--raw out.json]`. |
| `chart_server.py` | Daemon HTTP: POST `/chart` (→ `markdown` + `csv`), GET `/health`. Auto-cierre tras inactividad (default 3600 s). |
| `ocr_server.py` | **Daemon de texto + visión IA:** POST `/ocr` (textos + `missing`/`all_found`), POST `/ask` (pregunta en lenguaje natural, 4 motores), GET `/health`. |
| `ocr_verify.py` | Verificación visual de textos esperados en una captura (PP-OCRv6 / PaddleOCR-VL) + `--ask` con DocUnderstanding. Funciones reutilizables (`crear_modelo_ocr`, `predecir_textos`, `preguntar`...). |
| `vision360.py` | **Visión 360 híbrida:** Set-of-Marks + verdad de campo DOM + QA VLM por recortes. `python vision360.py --image shot.png --regions regions.json --engine ollama`. |
| `setup-ocr.ps1` | **Setup Windows del venv** (Python 3.11-3.13): crea `.venv-ocr` con paddlepaddle==3.3.1 + paddleocr[doc-parser]==3.7.0. |
| `requirements.txt` | Dependencias (paddlepaddle 3.3.1, paddleocr[doc-parser] 3.7.0, pandas). |
| `better_ocr.json.example` | Ejemplo de perfil por máquina (`{"perfil": "ligero", "ram_max_mb": 6000}`). |
| `tests/` | Pruebas unitarias (stdlib + pandas + pillow, sin paddleocr): extracción, cascada, vision, batería 360, ocr_server y vision360. |
| `scripts/verificar-proyecto.ps1` | **Verificación local completa** (port Windows del .sh upstream): sintaxis, tests, reglas P0/P1, config, seguridad y repo. |
| `scripts/hooks/pre-commit` | Hook git local (instalación: `cp scripts/hooks/pre-commit .git/hooks/pre-commit`). |
| `scripts/bateria_360.py` | Batería 360°: compara VLM locales (docbee / ollama) en 6 dimensiones; scoring automático + rúbrica humana. |
| `scripts/benchmark_ocr.py` | Benchmark de motores (ChartParsing / PP-StructureV3 / PP-OCRv6 / PP-OCRv5): carga, inferencia, RAM, puntuación. |
| `scripts/generar_charts.py` | Genera gráficos de prueba con datos CONOCIDOS + CSV de referencia (ground truth). |
| `scripts/validar_cascada.py` | Valida la ruta rápida contra los CSV de referencia de `ejemplos/test_charts/`. |
| `docs/GUIA_OCR_VISION.md` | **Documento general reutilizable** (Chart OCR, Text OCR y AI Vision con PaddleOCR). |
| `docs/LECCIONES-APRENDIDAS.md` | Memoria del proyecto: fallos, hallazgos y soluciones. |
| `docs/PRUEBAS.md` | Evidencia de pruebas del upstream y de las adaptaciones. |
| `docs/INTEGRACION_PROYECTOS.md` | **Cómo integrar este paquete en proyectos locales** (drilling-visualization, multistat, otros). |
| `ejemplos/` | Imágenes y CSV de prueba (`grafico_demo.png`, `test_charts/`). |

## Uso rápido (Windows)

```powershell
# 1. Entorno (crea .venv-ocr con el stack completo; ver GUIA_OCR_VISION.md)
powershell -ExecutionPolicy Bypass -File setup-ocr.ps1
#    Solo tests (sin paddle):  python -m venv .venv-ocr && .venv-ocr\Scripts\pip install pandas pillow

# 2. Crítico en Windows: %TMP% necesita > 3 GB libres para las descargas (OSError 122)

# 3a. Chart OCR directo
.venv-ocr\Scripts\python.exe chart_ocr.py ejemplos\grafico_demo.png --csv tabla.csv --raw bruto.json

# 3b. Daemon persistente (chart, auto-cierre tras 1 h sin peticiones)
.venv-ocr\Scripts\python.exe chart_server.py --port 8080
curl -X POST http://127.0.0.1:8080/chart -H "Content-Type: application/json" -d "{\"image\": \"ejemplos/grafico_demo.png\"}"

# 3c. Visión multi-modo (CLI)
.venv-ocr\Scripts\python.exe vision.py ejemplos\test_charts\bar_2series.png --modo graficos --salida csv
.venv-ocr\Scripts\python.exe vision.py ejemplos\test_charts\texto_boarding.png --modo texto
.venv-ocr\Scripts\python.exe foto.png --modo objetos          # RT-DETR: frutas/personas...
.venv-ocr\Scripts\python.exe imagen.png                       # auto: clasifica y rutea

# 3d. OCR de texto + visión IA por daemon
.venv-ocr\Scripts\python.exe ocr_server.py --port 8081
curl -X POST http://127.0.0.1:8081/ocr -H "Content-Type: application/json" -d "{\"image\": \"shot.png\", \"expected\": [\"DH001\"]}"
curl -X POST http://127.0.0.1:8081/ask -H "Content-Type: application/json" -d "{\"image\": \"shot.png\", \"query\": \"Cuantos paneles hay?\", \"engine\": \"ollama\"}"

# 3e. Vision 360 híbrida (SoM + DOM + QA por regiones)
.venv-ocr\Scripts\python.exe vision360.py --image shot.png --regions regions.json --engine ollama --ask-regions 1 3
```

## Perfiles por máquina (visión)

Cada equipo puede limitar los modos según su RAM, sin borrar código:

- `BETTER_OCR_PERFIL=completo` (default): sin límite, todos los modos.
- `BETTER_OCR_PERFIL=ligero`: máx. ~3500 MB por modo — permite `texto`, `graficos` (ruta rápida) y `objetos`; bloquea `doc` y el fallback VLM con un mensaje claro antes de cargar el modelo.
- Ajuste fino opcional: archivo `better_ocr.json` en la raíz, p. ej. `{"perfil": "completo", "ram_max_mb": 6000}`.

RAM medida por modo (MB): texto 1000, graficos rápido 1000, graficos VLM 5200, doc 4500, objetos 900.

## Pruebas

```powershell
# Sintaxis (sin dependencias)
.venv-ocr\Scripts\python.exe -m py_compile extractor_final.py chart_server.py chart_ocr.py ocr_rapido.py ocr_server.py ocr_verify.py vision.py vision360.py

# Pruebas unitarias (solo stdlib + pandas + pillow; paddleocr se simula)
.venv-ocr\Scripts\python.exe -m unittest discover -s tests -v
```

*Verificación local completa (sin GitHub/CI): `powershell -ExecutionPolicy Bypass -File scripts/verificar-proyecto.ps1`
ejecuta sintaxis + tests + checks de reglas, config y seguridad; el hook `pre-commit`
la ejecuta automáticamente antes de cada commit (instalación:
`cp scripts/hooks/pre-commit .git/hooks/pre-commit`).*

## Advertencias críticas (resumen)

- **Gráficos de líneas no garantizados:** en la prueba realizada el modelo no detectó la línea roja superpuesta. Validar antes de usar en producción.
- **RAM:** pico de carga de 4.8 GB (ChartParsing) y 6.4 GB (PP-StructureV3 con chart): no ejecutar nunca dos modelos VLM a la vez (OOM confirmado históricamente). PP-OCRv6 y RT-DETR son ligeros (~1 GB).
- **Concurrencia:** PaddleX no es thread-safe; serializar la inferencia (daemon persistente recomendado). Una sola instancia VLM por máquina.
- **Bug paddlepaddle 3.3.1 (PIR + oneDNN):** PP-OCRv6, PP-StructureV3 y RT-DETR fallan con `ConvertPirAttribute2RuntimeAttribute` si usan mkldnn. Workarounds en el código: `enable_mkldnn=False` (PaddleOCR/PPStructureV3) y `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` (RT-DETR). Detalle: issue PaddlePaddle/PaddleOCR#18162.
- **Cascada de gráficos:** la ruta rápida solo es fiable con categorías tipo año consecutivas y etiquetas de valor legibles; el gate la rechaza y cae al VLM ChartParsing en cualquier otra situación (líneas, pastel, scatter, etiquetas solapadas).
- **Límites de "visión 360°":** pinturas/dibujos/descripción de escenas requieren un VLM de captioning (PaddleOCR-VL 0.9B ≈ 4.7-9 GB). Objetos reales y personas: RT-DETR-L (validado, ~0.9 GB).
- **VLM locales en CPU (batería 360°):** gemma3:4b es el punto dulce (12/12 valores en ~150 s, RAM segura); tras usar modelos grandes con ollama, descárgalos siempre con `keep_alive=0` (dejan la RAM del host crítica). **Ollama no es servicio permanente**: el harness lo arranca bajo demanda y lo detiene al final.
- **docbee (PP-DocBee-2B): VALIDADO en GPU (RTX 3070 8 GB)** con `max_pixels` ≤ 0.5M px (OOM a resolución nativa); en lectura de valores queda por debajo de gemma por esa resolución limitada. Detalle: `docs/PRUEBAS.md` §4.1.
- **Imágenes de prueba externas:** contenido con derechos de terceros → solo en directorios temporales, nunca en el repo.

## Documentación oficial de referencia

- PaddleOCR — Módulo de gráficos (`chart_parsing`): `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/module_usage`
- PaddleOCR — Pipeline OCR general: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/OCR.en.md`
- PaddleOCR — Pipeline de comprensión de documentos: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/doc_understanding.md`
- PaddleOCR — Guía de instalación: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/installation.md`
