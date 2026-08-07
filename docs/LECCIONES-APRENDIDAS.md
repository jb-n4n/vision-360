# LECCIONES APRENDIDAS — extract-charts

> Memoria del proyecto (referenciada desde `AGENTS.md`). Se actualiza tras cada prueba, fallo o hallazgo relevante. Si algo falló 2+ veces, se documenta aquí con su solución.
> Anonimizado por regla P0.9: sin rutas de claves, cuentas ni datos personales.

## 1. Filtro de filas separadoras Markdown (2026-07-31)

**Fallo (1 vez, detectado en revisión):** la fila separadora `|---|---|` NO se eliminaba al convertir a DataFrame. El filtro original solo buscaba líneas que **empiezan** por `---`, pero el modelo puede emitir la fila con pipes iniciales (`| --- | --- |`), que nunca coincidía.

**Solución:** nueva función `es_fila_separadora()` en `extractor_final.py`: quita pipes de los bordes, divide por `|` y verifica que todas las celdas coincidan con `:?-{3,}:?` (guiones con alineación opcional). Cubre ambos formatos del modelo. Aplicado también al script de la guía (`docs/GUIA_OCR_VISION.md`).

**Refinamiento (segunda revisión, detectado por tests unitarios):** el regex inicial `:?-+:?` también clasificaba celdas de **dato** con guiones simples/dobles (`| - | - |`, ej. valores "-") como separadores, con pérdida de datos. El estándar markdown exige **3 o más guiones** en el separador: regex final `:?-{3,}:?`.

**Verificación:** prueba del servidor con modelo simulado + `tests/test_extraccion.py` (21 tests: `python3 -m unittest discover -s tests -v`).

## 2. Importación perezosa de PaddleOCR (2026-07-31)

**Fallo (1 vez, detectado en prueba):** `chart_server.py` no podía importarse ni probarse sin tener `paddleocr` instalado, porque la cadena de imports (`chart_server` → `extractor_final` → `paddleocr`) lo exigía al cargar el módulo.

**Solución:** `from paddleocr import ChartParsing` movido dentro de `main()` en `extractor_final.py`. Ahora los módulos se importan sin dependencias pesadas y el servidor es testeable con un modelo simulado.

## 3. Cierre del servidor y socket (2026-07-31)

**Fallo (1 vez, en prueba):** tras `server.shutdown()` el proceso de prueba colgaba: `shutdown()` detiene `serve_forever()` pero **no cierra el socket**; las conexiones nuevas quedaban en cola sin respuesta. El servidor real sí llama `server_close()` en el `finally`; la prueba no lo hacía.

**Solución:** en las pruebas, `server.server_close()` tras unirse al hilo de `serve_forever()`. Lección para código y test: tras `shutdown()` hay que `server_close()` para liberar el puerto.

## 4. `to_markdown()` requiere `tabulate` (2026-07-31)

**Fallo potencial (detectado en revisión):** `df.to_markdown()` en la respuesta del servidor depende del paquete `tabulate`, no declarado en el proyecto. En un entorno sin él, la respuesta de éxito rompería con 500.

**Solución:** `df_a_markdown()` en `chart_server.py` genera la tabla markdown manualmente (sin dependencias). El proyecto sigue usando solo: `pandas` + `paddlepaddle` + `paddleocr[doc-parser]`.

## 5. Permisos de opencode (better-ai, 2026-07-31)

**Lección del proyecto better-ai:** los patrones de permisos de `opencode.json` matchean **por tokens, no por subcadenas**; y ante empate de coincidencia **gana la última regla** (`last matching rule wins`). Por eso los `deny` específicos deben quedar DESPUÉS de cualquier `ask` genérico de su familia, y cada patrón se debe probar contra el comando real que debe bloquear. (Incluido en `CHECKLIST.md`.)

## 6. Los permisos de opencode se cargan al iniciar la sesión (2026-07-31)

**Fallo (1 vez, en prueba de cumplimiento):** la prueba "negarse a `rm -rf`" del ruleset FALLÓ: `rm -rf /tmp/opencode/rmtest` se ejecutó sin bloqueo a pesar de que `opencode.json` contiene `"rm -rf *": "deny"` (verificado, línea 91). Causa: el `opencode.json` se había añadido **a mitad de la sesión** y los permisos de opencode se cargan al **inicio de la sesión**; la sesión activa seguía con la configuración anterior.

**Solución:** los guardarraíles de `opencode.json` exigen **reiniciar opencode** tras crearlos/modificarlos para que se apliquen. La prueba de cumplimiento del ruleset (negación a `rm -rf`) debe ejecutarse en una sesión nueva; en esta sesión, la protección real fue la regla de texto P0.3, no el `deny`.

**Verificación:** sin daño (directorio desechable creado solo para la prueba); regla `deny` confirmada en el archivo. Pendiente de re-verificar en sesión nueva.

## 7. Validación con inferencia REAL (2026-07-31)

**Contexto:** primera ejecución real del proyecto (Python 3.11.9, Kubuntu, 16 cores, 15 GB RAM, PaddlePaddle 3.3.1, PaddleOCR 3.7.0) — el entorno documentado (Arch, Python 3.12) no estaba disponible.

**Hallazgo 1 — `libmklml_intel.so` no se encuentra (PaddlePaddle CPU en Kubuntu):** al cargar el modelo, fallo `RuntimeError: PreconditionNotMet ... libmklml_intel.so: cannot open shared object file`. La librería SÍ existe en `.venv/lib/python3.11/site-packages/paddle/libs/` pero no está en la ruta del loader. **Solución:** `export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.11/site-packages/paddle/libs:$LD_LIBRARY_PATH"` antes de ejecutar. (No era necesario en el entorno Arch original de la guía.)

**Hallazgo 2 — resultados reales de la validación (imagen oficial `chart_parsing_02.png`):**
- `extractor_final.py`: **6/6 valores exactos**, coinciden 1:1 con el ejemplo oficial de la documentación (2018–2023). Estructura de salida confirmada: `res.result`.
- `chart_server.py`: `GET /health` OK; `POST /chart` → 200 con 6 filas en **74 s** (inferencia en caliente, modelo ya cargado); apagado limpio por SIGTERM; **auto-cierre por inactividad verificado**: watchdog cerró el proceso tras 3605 s sin peticiones ("Proceso finalizado. Modelo descargado de la memoria.").

**Detalle operativo:** al lanzar `chart_server.py` desde una shell de herramientas que mata el grupo de procesos al expirar, el servidor recibe SIGTERM y cierra limpiamente (comportamiento correcto); para que sobreviva, lanzarlo con `setsid nohup ... & disown`.

## 8. Cláusulas Anti-Vibe-Code (P1.13–P1.18) y publicación en Codeberg (2026-08-01)

**Contexto:** sincronización con el ruleset [better-ai](https://github.com/jmbigi/better-ai) (ronda 33): nuevas reglas P1.13–P1.18 (autoría humana, disclosure `Assisted-by:`, anti-vibe-code, política de IA del anfitrión, humanos se comunican con humanos, revisión de imports antes de commit/push) y publicación del proyecto en Codeberg además de GitHub.

**Solución:** AGENTS.md, CHECKLIST.md y el verificador de ESTE proyecto actualizados (18 reglas P1); sección "Repositorio" en README con GitHub y Codeberg; remote `codeberg` añadido con el alias SSH `jmbigi-codeberg` (misma clave que GitHub, `IdentitiesOnly yes`, huella del host verificada contra docs.codeberg.org).

**Verificación:** verificador local: 15 OK, 1 FALLO conocido (`arbol de trabajo limpio`, esperado con cambios sin commitear) + 1 FALLO preexistente de tests documentado en la sección 9. HEAD idéntico en los tres remotos tras el push.

**Lección:** las reglas anti-vibe-code (P1.13–P1.17) son texto: según RepoComplianceBench (arXiv 2607.26819) los agentes cumplen disclosure/verificación (77–100%) pero no prohibiciones — el enforcement real está en la revisión humana y en los checks del verificador local (P1.18 concreta el check de imports/dependencias: existen, usados, seguros, licencias).

## 9. Test `test_chart_cuerpo_demasiado_grande` (2026-08-01)

**Hallazgo:** el test del límite de cuerpo (413) fallaba de forma consistente (5/5): `urllib.error.URLError: <urlopen error [Errno 32] Broken pipe>` en lugar de recibir el HTTP 413 esperado.

**Causa raíz (carrera TCP):** `chart_server.py` rechazaba el cuerpo con `_enviar_json(413, ...)` y cerraba la conexión sin drenar el cuerpo. El cliente aún estaba enviando el cuerpo de ~1 MB cuando el socket se cerraba → EPIPE en el cliente, que `urllib` reporta como `URLError` y no como `HTTPError(413)`.

**Solución (commit `93d340c`):** tras responder el 413 se drena el cuerpo de forma acotada (`self.rfile.read(min(largo, MAX_CUERPO))`) antes de cerrar; el cliente termina de enviar y recibe el 413. Suite 21/21 OK y hook pre-commit en verde.

**Lección:** en servidores HTTP propios, responder un error y cerrar sin drenar el cuerpo pendiente convierte el rechazo en una conexión rota para el cliente — drenar acotado tras el error evita el EPIPE sin exponer el servidor a lecturas ilimitadas.

## 10. Visión IA local para e2e: PP-OCRv6 + PP-DocBee-2B en GPU (2026-08-01)

**Contexto:** en visorweb2 los tests e2e se complementan con visión IA local (directiva better-ocr): el agente LLM de texto razona sobre los resultados de visión (JSON) en lugar de imaginar la UI.

**Solución (GPU 8 GB, RTX 30x0):**
- **Migración easyocr → PP-OCRv6** (`PaddleOCR`): mismo contrato `{text, confidence, bbox}`, memoria estimada `{'cpu': 3072, 'gpu': 2048}` MB. Cuellos de botella resueltos: `cu_seqlens` debe ser **int32** (`attention_mask`/`position_ids` con `.astype('int32')`) y redimensionar a **512 px** antes de pasar al DocBee.
- **LD_LIBRARY_PATH en subprocesos**: los bins del venv (paddle/libs + nvidia/*/lib) deben estar en `LD_LIBRARY_PATH`; **fix cuDNN**: resolver `libcudnn.so.8` como `libcudnn.so.9` para los bins de paddle.
- **SIGABRT in-process**: cargar PaddleOCR y correr capturas reales en el MISMO proceso del test aborta (cuDNN); los tests de screenshots reales corren en subproceso (`run_cli`) y la verificación GPU se hace vía CLI.
- **Bug bound-method en unittest**: `self.original = <método de clase>` liga el método a la instancia vía descriptor y contamina al test siguiente; usar `type(self).original`.
- **Contexto de captura (directiva "saber qué se captura")**: el e2e registra por pantallazo origen (headless viewport), frente/fondo, viewport, imagen y pageUrl; y el entorno del sistema (pantallas vía `xrandr`, escritorios virtuales vía `wmctrl -d`, sesión gráfica) — sin hostnames ni usuarios (P0.9).

**Verificación:** suite 46/46 (test_vision_analyze), e2e 4 estados: OCR real ('Instrument', 'Music Style', 'Siente el nacionalismo'), PP-OCRv6 posicionado (23 ítems) y PP-DocBee-2B respondiendo preguntas sobre la UI real.

**Lección:** la verificación de UI con visión local exige conocer el pipeline completo (resize, dtype, librerías dinámicas, subprocesos) — cada capa (OCR estructural, QA visual, contexto de captura) aporta un nivel de evidencia distinto al LLM de texto.

## 11. Revisión de robustez: 413/keep-alive, validación de imagen y mensajes 400 (2026-08-02)

**Contexto:** revisión del proyecto con reglas better-ai. Se investigó una hipótesis y se aplicaron dos mejoras con evidencia.

**Hallazgo 1 — el 413 NO es un bug de keep-alive (hipótesis refutada con evidencia):** se sospechó que tras el 413 el cuerpo residual sin drenar corrompería la siguiente petición de una conexión HTTP/1.1 keep-alive. Verificado con socket crudo y handler instrumentado (request line + `close_connection`): `BaseHTTPRequestHandler.protocol_version` por defecto es **HTTP/1.0**, y `parse_request` solo mantiene la conexión si `version_number >= (1,1) AND protocol_version >= "HTTP/1.1"` — como el servidor responde HTTP/1.0, **cierra la conexión tras CADA respuesta** y el residuo muere con el socket. El test con `http.client` que "sí" recibía 200 en la 2ª petición era engañoso: http.client reabre una conexión nueva al ver la respuesta HTTP/1.0. El drenado acotado del 413 (commit `93d340c`) es correcto y suficiente.

**Advertencia para el futuro:** si alguien activa `protocol_version = "HTTP/1.1"` en `chart_server.py`, el 413 quedará vulnerable: el cuerpo residual corrompería la request line de la siguiente petición de la misma conexión. En ese caso habría que añadir `Connection: close` a la respuesta 413 (o drenar el cuerpo completo).

**Mejora 1 — `validar_imagen` ahora verifica la firma mágica:** antes solo comprobaba `os.path.exists`, así que un directorio (`ejemplos`) o un `.txt` pasaban la validación y obligaban a cargar el modelo (3-5 min, 4.8 GB) para fallar después. Ahora `es_archivo_imagen()` lee los 16 primeros bytes y acepta solo firmas reales (PNG, JPEG, BMP, GIF, WebP, TIFF), con `ValueError` claro en caso contrario.

**Mejora 2 — el 400 del servidor distingue JSON inválido de clave faltante:** antes `{"otra": 1}` (JSON válido) devolvía `"JSON invalido: 'image'"`, un mensaje falso. Ahora: `"Se espera un objeto JSON con la clave 'image'"`; también rechaza `"image"` que no sea string (evita un 500 por un error del cliente).

**Verificación:** 27/27 tests (`python3 -m unittest discover -s tests -v`), sintaxis OK, verificador local 20/20 OK.

## 12. Visión multi-modo: cascada PP-OCRv6, benchmark de motores y límites de la máquina (2026-08-05)

**Contexto:** extensión del proyecto a "visión IA": modos texto/gráficos/doc/objetos con CLI (`vision.py`) y servidor (`POST /vision`), más una ruta rápida de gráficos (`ocr_rapido.py`) que compite con el VLM ChartParsing.

**Benchmark real (imagen oficial chart_parsing_02, 1 ejecución por motor, subprocesos aislados):**

| Motor | Carga | Inferencia | RAM pico | Exactitud |
|---|---|---|---|---|
| `ChartParsing` (VLM, base) | 147 s | 179 s | 5.2 GB | 18/18 celdas |
| `PP-StructureV3` (con chart) | 73 s | 266 s | 6.4 GB | 12/18 (beneficios 0/6: peso PP-Chart2Table cargado con `embed_tokens` sin inicializar) |
| `PP-OCRv6` (texto) | 16 s | 38 s | 1.0 GB | 12/12 valores + 6/6 años |
| `PP-OCRv5` (texto) | 7 s | 51 s | 2.7 GB | 12/12 + 6/6 |

**Hallazgo 1 — bug paddlepaddle 3.3.1 (PIR + oneDNN):** PP-OCRv6/v5, PP-StructureV3 y RT-DETR fallan al cargar con `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`. Confirmado por mantenedores como bug del framework (issue PaddlePaddle/PaddleOCR#18162); workaround oficial: desactivar oneDNN. Soluciones aplicadas y verificadas: `PaddleOCR(enable_mkldnn=False)`, `PPStructureV3(enable_mkldnn=False)`, y para PaddleX genérico (RT-DETR) `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` **antes** del import (el flag se lee al importar paddlex; `run_mode`/`enable_mkldnn` por kwargs NO llegan al runner, verificado). Los flags `FLAGS_use_mkldnn=0` y `FLAGS_enable_pir_api=0` NO funcionan aquí.

**Hallazgo 2 — `PPStructureV3.predict` no acepta dict:** solo `str`/`ndarray`; con `{"image": ruta}` devuelve lista vacía sin error (`IndexError` al indexar). El resultado JSON expone la estructura `res.layout_det_res.boxes` (no `layout`).

**Hallazgo 3 — OCR doble (imagen completa + banda inferior):** PP-OCRv6 pierde las etiquetas del eje X cuando las etiquetas de valores negativos se solapan con ellas; un segundo OCR del 14% inferior (crop) las recupera. Fusión con regla: conflicto por IoU gana la lectura de mayor score (con epsilon 1e-4: `1.0 > 0.999991` es ruido de punto flotante y NO debe reemplazar); duplicado por texto idéntico con centros cercanos se descarta. La banda NO debe ser demasiado estrecha (corta glifos) ni demasiado ancha (corrompe signos de valores).

**Hallazgo 4 — el gate de plausibilidad es quien evita la pérdida silenciosa de datos:** un año sin valor leído (`-2.9` en bar_2series) o número inconsistente de columnas entre años hace FALLAR la ruta rápida y cae al VLM (18/18). El fast path gana tiempo/RAM solo cuando está completo; nunca devuelve tablas incompletas.

**Hallazgo 5 — generación de gráficos de prueba (matplotlib):** el OCR no lee texto pequeño (xtick 10pt a dpi 150 = 0 detecciones). Además: offset positivo en etiquetas de valores negativos las cruza con la línea del eje (el OCR lee la caja vacía, score 0.0) y con las etiquetas de años (se fusionan en una detección). Solución: `figsize` generoso, xtick ≥ 14pt, valores negativos etiquetados DEBAJO de la barra con offset −2, margen inferior amplio. En el fast path, los ticks del eje Y quedan a ≥0.5×espaciado de la primera categoría y las etiquetas reales a ≤0.3×: ventana de emparejado 0.5×espaciado discrimina ambos (verificado en matplotlib y plotly).

**Hallazgo 6 — esta máquina tiene 7 GB de RAM (no 16 como la lección 7):** ChartParsing (5.2 GB) cabe justo; PP-StructureV3 con chart (6.4 GB) roza OOM; layout-only (4.5 GB) cabe. PaddleOCR-VL 0.9B (4.7–9 GB) NO cabe: los modos pinturas/dibujos/descripción quedan fuera en este hardware y requieren GPU o más RAM. Modos validados aquí: texto (PP-OCRv6), graficos (cascada), doc (layout-only, 323 s), objetos (RT-DETR-L, 18 s, 857 MB, 7 detecciones en foto real de frutas).

**Verificación:** 56/56 tests, e2e del servidor `/vision` real (35 líneas de la tarjeta de embarque en 61 s), informe `/var/tmp/better-ocr-bench/reporte.json`.

**Lección:** ante un bug de framework, verificar en la fuente (issues oficiales) y aplicar el workaround oficial; los parámetros de librerías de alto nivel (PaddleX) a veces no propagan lo que prometen — hay que leer el código de flags del paquete instalado. Y medir RAM real por modo antes de prometer "visión total" en una máquina concreta.

## 13. deepseek-ocr.rs (Rust) con PaddleOCR-VL q4k: funciona y es exacto, pero lento (2026-08-05)

**Contexto:** prueba mínima de la alternativa en Rust (sin Python) en esta máquina (7.7 GB RAM, 9 GiB swap añadido por el programador como root, CPU).

**Build:** `cargo build --release -p deepseek-ocr-cli` con rustup (perfil minimal, instalado en `~/.cargo`). **Dos fallos de build con causas distintas:** (1) `Bus error (señal 7)` del linker (rust-lld/LLVM): el repo se clonó en `/tmp` que es **tmpfs de 3.9 GB** — el enlazado llena tmpfs y muere; solución: `CARGO_TARGET_DIR` y `TMPDIR` en disco (`/home/admin/dsocr-target`). (2) `ring` build script falló una vez por presión de disco mientras el programador creaba `/swapfile2` (4 GiB): transitorio, recompilando pasó.

**Resultado real (grafico_demo.png, `--model paddleocr-vl-q4k --device cpu --max-new-tokens 400`):** 12/12 valores + 6/6 años **exactos** (q4k NO degradó los dígitos en este caso, contrariamente a la predicción inicial). Carga del modelo 8.2 s. **~1200 s de inferencia** (prefill visión 0.43 tok/s en 477 tokens = 1099 s; generation 134 tokens a 1.4 tok/s = 96 s). Sin OOM gracias al swap (9 GiB). RAM real no medida (el monitor VmRSS leyó el PID del wrapper, no el proceso — monitoreo no fiable con `&` dentro del shell; medir con subproceso directo).

**Comparativa final en este gráfico:** PP-OCRv6 fast path 57 s/1 GB (gate rechaza), ChartParsing 333 s/5.2 GB/18-18, deepseek-ocr.rs q4k ~1200 s/~4-6 GB/12-12, PP-StructureV3 345 s/6.4 GB/12-18.

**Lección:** la alternativa Rust es viable y exacta, pero en CPU es ~4× más lenta que el VLM Python y ~20× que PP-OCRv6: solo para batch sin prisa o entornos sin pila Python. Y un swapfile nuevo con `mkswap -U clear --size 4G --file` + `pri=10` en fstab (verificado con `findmnt --verify`: 0 errores) es seguro si no se toca el swap existente.

## 14. Matriz de validación completa del set de charts (2026-08-05)

**Contexto:** cierre de la validación de la cascada sobre los 8 gráficos de `ejemplos/test_charts/` + el demo oficial, con fallback VLM real (`--con-fallback`).

| Chart | Fast path | Fallback ChartParsing | Resultado |
|---|---|---|---|
| bar_2series (mpl, 2 series) | 6/6 + 12/12 (~70 s, 1 GB) | — | OK |
| plotly_barra (plotly) | 6/6 + 12/12 (~58 s, 1 GB) | — | OK |
| grafico_demo (oficial) | rechaza (año truncado) | 18/18 (~333 s, 5.2 GB) | OK |
| pie_5 | rechaza (sin años) | 5/5 + 5/5 (~209 s) | OK |
| line_3series | rechaza | 3/3 + 24/24 (~275 s) | OK |
| seaborn_agrupado | rechaza | 4/4 + 8/8 (~248 s) | OK |
| bar_apilada | rechaza | 3/3 + 12/12 (~236 s) | OK |
| scatter_valores | rechaza | 0/6 + 0/12 — **el VLM alucina una tabla** (1999/2008/2010 con valores inventados) | NO soportado |

**Hallazgo 1 — ChartParsing transpone filas/columnas según el tipo de gráfico:** en line y stacked-bar emite filas = series y columnas = categorías (línea: `A|M1..M8`; apilada: `Part A|Q1..Q4`), mientras que en barras agrupadas emite filas = categorías. Las referencias de validación se ajustaron al formato real del modelo (lección: el ground truth debe replicar el formato de salida del motor, no el "natural" del dataset).

**Hallazgo 2 — el scorer debe tolerar sufijos:** los valores de pie salen como `35.0%`; `normalizar()` ahora quita `%` antes de comparar. Un 0/5 no era fallo del modelo sino del validador.

**Hallazgo 3 — scatter no es soportado por PP-Chart2Table:** el modelo (entrenado para barra/línea/pastel) inventa datos plausibles en lugar de devolver los labels reales de los puntos. Peor que un error: es una falsa tabla. Para scatter, la lectura útil es solo el modo texto (PP-OCRv6 lee los labels `x,y`), sin reconstrucción de tabla.

**Verificación:** 59/59 tests, matriz completa con corridas reales (2× fast path + 5× fallback VLM + 1 rechazo documentado).

## 15. Batería 360° VLM en CPU: ollama, gemma3:4b y qwen2.5vl (2026-08-05)

**Contexto:** comparación empírica de VLM locales con el harness `scripts/bateria_360.py` (6 dimensiones: QA de UI, interpretación, valores, objetos, descripción, documento) en un servidor CPU con 7.7 GB de RAM.

**Instalación de ollama sin pipes (P0.8):** el instalador oficial usa `curl | sh` (prohibido por el guardarraíl); alternativa verificada: descarga del tarball oficial desde el release de GitHub (`ollama-linux-amd64.tar.zst`, ~1.35 GB) y extracción local. El servidor se lanza a mano en `127.0.0.1:11434`.

**Resultados medidos (misma batería, temperatura 0):**

| Test | qwen2.5vl:3b | gemma3:4b | qwen2.5vl:7b |
|---|---|---|---|
| valores (demo oficial) | 12/12 (224 s) | 12/12 (150 s) | 12/12 (399 s) |
| valores (pie) | — | 5/5 (130 s) | 5/5 (585 s) |
| objetos (frutas) | 3/4 | 3/4 | 3/4 |
| documento | — | 1/2 (371 s) | 1/2 (324 s) |

**Hallazgo 1 — empate técnico en la batería objetiva:** los tres modelos puntúan igual (12/12, 3/4, 1/2); la diferencia es tiempo y RAM. gemma3:4b es el punto dulce de la máquina (2.5-4.5× más rápido que el 7b).

**Hallazgo 2 — qwen2.5vl:7b acorrala la RAM del servidor:** carga 5.8 GB; al terminar dejó 150 MB libres (riesgo real para los servicios del host). Solución verificada: descargar el modelo tras cada uso con la API `keep_alive=0` (`POST /api/generate {"model": ..., "keep_alive": 0}`). Nunca dejar modelos grandes cargados en un servidor con servicios productivos.

**Hallazgo 3 — el monitor del servidor marcaba "Status: error" por disco al 91%** (umbral >90), no por servicios: `ollama`/`vsftpd` "not-found" son informativos y no cuentan para el estado. La clave `services_error` en realidad significaba "cualquier error": renombrada a `any_error` (nadie la consumía; verificado). Con disco a ~63% el monitor volvió a `ok`.

**Verificación:** 66/66 tests, corridas reales documentadas; salidas crudas en `/var/tmp/bateria360/`.

## 16. Descripción de imágenes reales: el 7b mejora, pero modestamente (2026-08-05)

**Contexto:** prueba diminuta (2 fotografías reales de web pública, sin datos personales) con la misma pregunta de descripción a gemma3:4b, qwen2.5vl:7b y un modelo comercial de referencia.

**Imagen 1 (parque con plataformas circulares, junto a cancha cercada):** el 7b acertó el contexto que gemma perdió — autobús claro, casas urbanas al fondo, portería tras la red — y coincidió con la referencia comercial en 4 detalles de contexto; gemma atribuyó el naranja del autobús a "marcas del campo". Ambos fallaron las máquinas de gimnasio biosaludable y el detalle de un niño con camiseta blanca/pantalón rojo (solo la referencia comercial lo captó).

**Imagen 2 (siete niños corriendo tomados de la mano por un camino de tierra):** ambos contaron 7, vieron manos, camino y bosque; el 7b fue más específico en colores reales (rosa, mezclilla) frente al genérico "blues/whites" de gemma; la referencia comercial añadió el desglose completo de atuendos por niño (nivel que ningún modelo local alcanza).

**Conclusión (n=2):** el 7b mejora la calidad descriptiva de forma real pero modesta — contexto de escena y especificidad de color — y nunca pierde frente a gemma; ambos locales quedan por debajo de la referencia comercial en granularidad. Las imágenes externas no se commitean al repo (contenido con derechos; solo en directorio temporal).

**Lección:** para decidir entre modelos hay que probar con contenido real y una referencia independiente; la batería objetiva puede empatar mientras la calidad descriptiva difiere — por eso la batería 360° incluye dimensiones libres con rúbrica humana.

## 17. docbee (PP-DocBee-2B) en GPU: cinco bugs de integración resueltos (2026-08-05)

**Contexto:** cierre del pendiente "docbee en GPU" (RTX 3070 Laptop 8 GB, driver 580, CUDA 13, paddlepaddle-gpu 3.3.1 build cu126 + paddlex 3.7.2). La batería 360° había corrido solo ollama; docbee nunca había arrancado. Cada bug se resolvió de a uno, con evidencia.

1. **`cuda` no es un device válido para paddlex:** `DocVLM(device="cuda")` lanza `AssertionError` — `SUPPORTED_DEVICE_TYPE` en `paddlex/utils/device.py` solo acepta `cpu|gpu|xpu|npu|mlu`. El script pasaba `--device cuda` directo. Fix: normalizar `cuda*` → `gpu` en `run_docbee`.
2. **DocVLM espera `query`, no `prompt`:** el input dict correcto es `{"image": ..., "query": ...}` (CLI help del propio `doc_vlm.py`). Con `prompt` → `KeyError: 'query'` en el processor. La batería nunca lo había usado, por eso no se detectó.
3. **paddle 3.3.1 GPU + flash attention: `paddle.cumsum` promueve int32 → int64**, y `flash_attn_unpadded` exige cu_seqlens int32 → `InvalidArgument` y SIGABRT en el decode (exacto: `flash_attn_unpadded(q,q,q,cu,cu,...)` con cu int64 falla y con int32 funciona, verificado con repro mínimo). El código de paddlex (`_get_unpad_data`) calcula int32 pero cumsum lo promueve. Fix: parche en el subproceso que fuerza `cu.astype('int32')`.
4. **OOM en 8 GB a resolución nativa:** `MAX_PIXELS = 16384*28*28` (12.8M px) genera miles de tokens y el `cast` de logits a fp32 (seq × 151936 vocabulario) desborda el pool de ~7.5 GB (el allocator reserva 95%). Con `MAX_PIXELS=262144` (0.5M px ≈ 245 tokens) todo cabe (verificado: pie 5/5). El flag `FLAGS_fraction_of_gpu_memory_to_use=0.98` NO sirve (CUBLAS error 15).
5. **LD_LIBRARY_PATH del host sombrea el CUDNN del venv:** el shell exporta rutas `nvidia/` del pyenv (CUDNN 9.1); paddle 3.3.1 (compilado con CUDNN 9.5) carga `libcudnn_graph.so.9` viejo → `undefined symbol: cudnnGetLibConfig` + SIGABRT. Fix en `run_docbee`: anteponer `venv/site-packages/nvidia/*/lib` y quitar rutas `.pyenv` del env del subproceso.

**Resultado:** 8/8 tests de docbee en GPU (3.5–12 s por test, salvo descripcion 205 s y documento 165 s), ~7.1 GB de RAM. vs gemma3:4b: docbee gana ui_qa (4/4 vs 2/4) y personas (1/2 vs 0/2); gemma gana valores y velocidad — pero docbee corrió con resolución limitada (0.5M px) por el OOM, así que la comparación de valores es injusta a favor de gemma. Ver PRUEBAS.md §4.1.

**Lección:** en GPU de 8 GB los VLM 2B caben solo con max_pixels reducido; y cada capa (device name, key del input, dtype de índices, pool de memoria, shadowing de libs dinámicas) es una fuente real de fallo que no aparece en CPU — el harness debe ejecutarse en la máquina objetivo antes de declarar un motor "validado".


## 18. Experimento captcha real: loop completo y sus limites (2026-08-06)

**Contexto:** objetivo adicional y secundario â€” resolver un reto visual tipo captcha (reCAPTCHA v2) con el stack local desde un NAVEGADOR TEMPORAL (Playwright, contexto fresco desechable: sin perfil ni cookies). Modo REAL por default (decision del programador), --local para demo sintetica 3x3 determinista.

**Lo que funciona (verificado en vivo):** ciclo completo checkbox -> reto en iframe bframe -> instruccion leida del DOM (con fallback OCR) -> captura de la cuadricula 3x3/4x4 -> resolucion -> clics JS en los tiles -> VERIFY (o SKIP) -> veredicto real por el estado del checkbox del ancla (rc-anchor-checkbox-checked).

**Hallazgos y lecciones:**
1. **El clic normal de Playwright falla en los tiles** ("element is outside of the viewport"): reCAPTCHA posiciona los tiles con transforms anti-automatizacion. Fix: click JS (el.click()) â€” verificado que registra la seleccion (clase rc-imageselect-tileselected).
2. **El reto real expira en ~2 min**: el VLM (qwen2.5vl:3b en CPU) tarda 2-3 min y sobre-selecciona (dijo 7,8,9,10,11 cuando habia 1 moto). Para objetos COCO se usa SOLO RT-DETR (~20-40 s, cabe en la ventana); el VLM queda para no-COCO (crosswalks, stairs...).
3. **Objetos pequenos puntuan ~0.5-0.6** (bicicleta real a 0.55, bajo el umbral 0.6). Fix: umbral 0.45 para la clase objetivo del reto (0.6 para el resto).
4. **Deteccion por celda con upscale 2x** (LANCZOS) sube los scores y la resolucion de objetos pequenos (n*n llamadas /vision; tolerante a fallos por celda).
5. **Parser de instrucciones**: prefijos "select all images/squares/tiles with", articulos ("a fire hydrant"), plurales irregulares (buses, crosswalks), y recorte del texto accesorio CONCATENADO sin espacio ("traffic lightsIf there are none, click skip") + flag permite_skip.
6. **Precision contra retos reales ~50-70% (esperado, confirmado)**: 3x3 buses -> los 3 buses detectados y aun asi rechazado (imagenes adversarias: buses ocultos que el modelo no ve o falsos positivos). 4x4 motorcycles -> celda 12 acertada en el detector.
7. **Estabilidad**: la sesion paralela reinicia el daemon a mitad de ejecucion y corta el loop (el experimento murio con exit -1 sin traceback; el daemon registro el ConnectionReset del cliente). El loop por celda ahora tolera fallos por celda.

**Pendientes (seguir):**
1. Verificar la pasada por celda offline sobre la cuadricula 4x4 de motos guardada (captcha_reto_grid.png en temp) — el daemon se reinicio a mitad y corto la prueba. **Nota (2026-08-07):** el mapeo bbox->celda quedo cubierto por tests de regresion (`tests/test_experimento_captcha.py`): la pasada COMPLETA mapea por centro del bbox en espacio de imagen completa (correcto, 1/5/9), y la pasada POR CELDA asigna siempre al numero del loop (la posicion del bbox dentro del recorte no cambia la celda). Pendiente: re-generar la cuadricula real y verificar la pasada por celda sobre ella.
2. ~~Reintento automatico de 2-3 rondas cuando el reto se re-renderiza tras un rechazo.~~ **HECHO (2026-08-07):** `_loop_reto` + `--intentos` (default 3) + `_esperar_reto_nuevo` (detecta el re-render por el cambio del src absoluto del primer tile `img.rc-image-tile` en el bframe; tolerante si no lo detecta). El bucle se extrajo a funcion pura para testearlo sin playwright: 4 tests en `tests/test_experimento_captcha.py` (verificador: 106 tests OK). Pendiente: validacion en vivo del re-render (el src cambia de verdad en el demo real).
3. ~~Evaluar /vision por CLI (subproceso) en vez de n*n llamadas HTTP al daemon, o reintento por celda, para resistir reinicios del daemon.~~ **HECHO (2026-08-07):** se eligio la opcion del **reintento por celda** (`_llamar_con_reintentos`, 2 intentos con 3 s de espera; celda saltada solo si ambos fallan) en vez de la CLI/subproceso, que cargaria una instancia extra de RT-DETR en memoria (evitar duplicar modelos; leccion 16). Helper puro testeable: 5 tests nuevos (helper + cableado con mock de `_post`, grid PIL 60x60): 111 tests OK en el verificador. Pendiente: validacion en vivo simulando el corte del daemon a mitad del loop.
4. Validar SKIP en vivo (reto crosswalks con "If there are none, click skip").
