# 📊 GUÍA TÉCNICA — OCR DE TEXTO, CHART OCR Y AI VISION CON PADDLEOCR (CPU · Python 3.12)

**Fecha de emisión:** Julio 2026
**Entorno de prueba:** Linux, CPU Intel, Python 3.12, PaddlePaddle 3.3.1, PaddleOCR 3.7.0
**Segunda validación real:** Kubuntu, Python 3.11.9 (16 cores) — extracción 6/6 valores exactos con la imagen oficial `chart_parsing_02.png`; servidor: 74 s de inferencia en caliente y auto-cierre por inactividad verificado. Gotcha específico de este sistema: `libmklml_intel.so` requiere `export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.11/site-packages/paddle/libs:$LD_LIBRARY_PATH"` (no era necesario en Arch).
**Estado:** **Verificado parcialmente** mediante ejecución real en CPU. Todas las afirmaciones han sido contrastadas con la documentación oficial de PaddleOCR/PaddleX. Las conclusiones se limitan estrictamente a las pruebas descritas.

> **Uso de este documento:** es autónomo y está pensado para copiarse en otros proyectos. Contiene tres capacidades con el mismo ecosistema (PaddleOCR) y las mismas lecciones operativas: **Chart OCR**, **Text OCR** y **AI Vision**.

---

## 1. ALCANCE Y DECLARACIÓN DE HONESTIDAD

Este documento describe la extracción de datos e información desde imágenes con tres pipelines de PaddleOCR 3.7, todos ejecutables en CPU:

| Pipeline | Clase Python | Capacidad |
| :--- | :--- | :--- |
| **Chart OCR** | `ChartParsing` (modelo PP-Chart2Table) | Gráfico → tabla de datos (Markdown/CSV) |
| **Text OCR** | `PaddleOCR` (modelo PP-OCRv6) | Imagen → textos reconocidos (cualquier idioma) |
| **AI Vision** | `DocUnderstanding` (modelos PP-DocBee) | Imagen + pregunta → respuesta en lenguaje natural (entendimiento visual general) |

**Lo que ha sido probado y verificado en este documento (Chart OCR):**

- Extracción correcta de **6/6 valores exactos** en un gráfico de barras aleatorio (coincidencia numérica perfecta).
- Gestión del error `OSError(122)` mediante la variable `TMPDIR`.
- Consumo de recursos (RAM, tiempo, espacio en disco) del modelo PP-Chart2Table.
- Comportamiento en escenarios de múltiples instancias y concurrencia (lecciones de la Sección 7).

**Lo que NO ha sido probado y, por tanto, no se garantiza:**

- La extracción de series de líneas con Chart OCR. En la prueba realizada, el modelo **no detectó la línea roja** superpuesta al gráfico de barras.
- El rendimiento en gráficos circulares, de dispersión o con ejes logarítmicos.
- Las mediciones de rendimiento de **Text OCR** y **AI Vision** (no medidos en este entorno; se indican solo los datos oficiales).

PP-Chart2Table anuncia soporte para múltiples tipos de gráficos; sin embargo, **este documento solo valida su funcionamiento para barras en el entorno especificado**.

---

## 2. INSTALACIÓN

### 2.1 Instalar PaddlePaddle para CPU (versión estable 3.3.1)

```bash
pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

### 2.2 Instalar PaddleOCR según la capacidad

```bash
# Text OCR (pip install paddleocr es suficiente; sin dependencias extra)
pip install -U paddleocr

# Chart OCR y AI Vision (requieren el grupo de parsing de documentos)
pip install -U "paddleocr[doc-parser]"
```

**Notas oficiales (PaddleOCR 3.7.0):**

- **Python:** se requiere 3.8+ (grupo `doc-parser` requiere 3.9+). Compatible con Python 3.12.
- **Motor de inferencia por defecto:** `paddle_dynamic` en la versión 3.7.0 (en versiones 3.x recientes sustituye al antiguo `paddle`). Alternativas disponibles mediante el parámetro `engine`: `transformers` o `onnxruntime`.
- **Fuente de modelos:** HuggingFace por defecto. Si el acceso no es posible, cambiar el origen con `PADDLE_PDX_MODEL_SOURCE="BOS"`.
- **Modelos:** se descargan automáticamente en la primera ejecución y se almacenan en `~/.paddlex/official_models/`.

### 2.3 ⚠️ ADVERTENCIA CRÍTICA: ESPACIO EN /tmp (Error OSError 122)

Los modelos se descargan utilizando un buffer temporal en `/tmp` antes de ser almacenados en `~/.paddlex/official_models/`. Si tu sistema monta `/tmp` con un tamaño limitado (ej. tmpfs de 3.9 GB), la descarga fallará con `OSError(122): Disk quota exceeded`.

**Solución obligatoria antes de la primera ejecución:**

```bash
# Redirige el buffer de descarga a una ubicación con suficiente espacio (> 3 GB)
export TMPDIR=/var/tmp   # O cualquier otra ruta de tu sistema con espacio
```

*(En Windows, configura la variable de entorno `TMP` o `TEMP` apuntando a una unidad con espacio suficiente).*

---

## 3. HERRAMIENTAS CONTRASTADAS (SOLO CHART OCR)

| Herramienta | Estado | Observaciones verificadas |
| :--- | :--- | :--- |
| **PP‑Chart2Table** (PaddleOCR) | ✅ **Recomendada (con reservas)** | Modelo VLM de **0.58B parámetros** (ficha oficial). Ocupa **1.4 GB** en disco según la ficha técnica. **Nota:** el peso en disco medido en este entorno fue de 2.24 GB (2,242,172,856 bytes), posiblemente por diferencias de formato o pesos adicionales. El modelo fue **actualizado el 2025‑06‑27** (la ficha oficial ofrece el peso anterior como `.bak`), lo que puede explicar la discrepancia. |
| **plotdigitizer** | ⚠️ Limitada | Solo curvas simples, blanco/negro y calibración manual. No apta para mixtos. |
| **Nemotron Graphic Elements** | ✅ Existente | No probado en este entorno. |
| **Charter** | ❌ **Inexistente** | No se encuentra en PyPI (error 404). |
| **extract-line-chart-data** | ❌ **Inexistente** | Herramienta ficticia sin soporte. |

---

## 4. CHART OCR — GRÁFICO → TABLA DE DATOS (PP-Chart2Table)

Convierte gráficos (barras, líneas, etc.) en su tabla de datos subyacente, en formato Markdown.

### 4.1 Ejemplo oficial de integración

```python
from paddleocr import ChartParsing

model = ChartParsing(model_name="PP-Chart2Table")
results = model.predict(input={"image": "grafico.png"}, batch_size=1)
for res in results:
    res.print()
    res.save_to_json("./output/res.json")
```

### 4.2 Script validado (`extractor_final.py`)

El siguiente script ha sido probado y maneja las inconsistencias en la estructura de salida de la API. **Importante:** el método `predict()` devuelve objetos `Result` de PaddleX, que **no son directamente serializables con `json.dump()`**. El script utiliza la propiedad `.json` para obtener un diccionario serializable.

**Nota:** se especifica `device="cpu"` explícitamente porque, según la documentación oficial, el valor por defecto prioriza GPU 0 y solo usa CPU si no hay GPU disponible.

```python
import json
import re
import pandas as pd
from io import StringIO
from paddleocr import ChartParsing


def es_fila_separadora(linea):
    """True si la linea es una fila separadora de tabla markdown.

    Cubre los dos formatos posibles del modelo: '--- | ---' (sin pipe inicial)
    y '| --- | --- |' (con pipes). Cada celda debe ser solo guiones (3 o mas,
    como exige el estandar markdown), opcionalmente con ':' de alineacion
    (':---', '---:', ':---:'). Un guion simple o doble es un dato, no un
    separador.
    """
    celulas = [c.strip() for c in linea.strip().strip('|').split('|')]
    celulas = [c for c in celulas if c != '']
    return bool(celulas) and all(re.fullmatch(r':?-{3,}:?', c) for c in celulas)


# 1. Inicializar el modelo (la primera ejecución descargará el modelo en TMPDIR)
model = ChartParsing(device="cpu")  # device explícito: el default prioriza GPU si existe

imagen = "ruta/a/tu/grafico.png"

# 2. Ejecutar la predicción
resultados = model.predict({"image": imagen})  # Devuelve una lista de objetos Result

# 3. Verificar que la lista no esté vacía
if not resultados:
    raise RuntimeError("No se obtuvo ningún resultado del modelo. Verifica la imagen.")

# 4. Guardar JSON completo para depuración (usando .json para serializar)
res = resultados[0]
with open("salida_bruta.json", "w", encoding="utf-8") as f:
    # .json convierte el objeto Result a un dict serializable
    json.dump(res.json, f, indent=2, ensure_ascii=False)

# 5. Acceso robusto a la clave 'result'
# La estructura puede variar: 'result' en raíz o dentro de 'res'
markdown_tabla = None

if "result" in res.json:
    markdown_tabla = res.json["result"]
elif "res" in res.json and "result" in res.json["res"]:
    markdown_tabla = res.json["res"]["result"]

if not markdown_tabla:
    print("Estructura JSON recibida:", json.dumps(res.json, indent=2))
    raise KeyError("No se encontró la clave 'result' en la respuesta. Revisa salida_bruta.json.")

# 6. Limpieza del Markdown (eliminar filas separadoras y espacios extremos)
lineas = markdown_tabla.splitlines()
lineas_filtradas = [
    linea for linea in lineas
    if not es_fila_separadora(linea) and linea.strip() != ''
]
markdown_limpio = "\n".join(lineas_filtradas).strip()

# 7. Convertir a DataFrame (el separador es pipe '|' con espacios)
df = pd.read_csv(StringIO(markdown_limpio), sep=r"\s*\|\s*", engine="python")

# 8. Limpieza de columnas fantasmas (generadas por pipes al inicio/final)
df = df.dropna(axis=1, how='all')
df = df.loc[:, ~df.columns.str.contains('^Unnamed')]

# 9. Guardar resultado
df.to_csv("datos_extraidos.csv", index=False, encoding='utf-8-sig')
print("\n[OK] CSV guardado como 'datos_extraidos.csv'")
print("Primeras filas del dato extraído:")
print(df.head())
```

**Estructura oficial de salida** (ejemplo de la documentación):

```text
{'res': {'image': 'chart_parsing_02.png',
         'result': '年份 | 单家五星级旅游饭店年平均营收 (百万元) | 单家五星级旅游饭店年平均利润 (百万元)\n2018 | 104.22 | 9.87\n...'}}
```

### 4.3 Especificaciones técnicas (mediciones reales y datos oficiales)

| Parámetro | Valor | Origen del dato |
| :--- | :--- | :--- |
| **Parámetros del modelo (oficial)** | **0.58B** | Documentación oficial de PaddleOCR |
| **Tamaño en disco (oficial)** | **1.4 GB** | Ficha técnica del modelo |
| **Tamaño en disco (medido)** | **2.24 GB** (2,242,172,856 bytes) | Medición directa en el sistema de pruebas |
| **Ubicación de los pesos** | `~/.paddlex/official_models/` | Observado durante la ejecución |
| **Consumo de RAM (pico)** | **4.8 GB** (durante la carga) | Medido mediante monitoreo del proceso |
| **Consumo de RAM (predicción)** | ~2.5 GB | Medido durante la inferencia |
| **Tiempo de inferencia (CPU)** | **162 – 286 segundos** (3 a 5 minutos) | Medido en 3 ejecuciones; varía según la carga del sistema y resolución de la imagen |
| **Tiempo de inferencia (GPU A100, oficial)** | **17.9 s** (`paddle_dynamic`) / **12.2 s** (`transformers`) | Benchmark oficial (end‑to‑end, imagen de demostración) |
| **Exactitud (prueba de barras)** | **6/6 valores exactos** | Coincidencia perfecta con los valores originales del gráfico de prueba. **Esta cifra se limita a un solo caso y no representa una validación estadística general.** |
| **Model Score (oficial)** | **80.60** | Evaluación interna sobre 1801 muestras |

---

## 5. TEXT OCR — IMAGEN → TEXTO (PP-OCRv6)

Reconocimiento de texto impreso/manuscrito en imágenes, con soporte multilingüe. El pipeline combina detección de texto, clasificación de orientación, enderezado y reconocimiento. **Modelo por defecto:** PP-OCRv6_medium (publicado con PaddleOCR 3.7), un solo modelo con soporte para 50 idiomas (chino, inglés, japonés y 46 alfabetos latinos).

### 5.1 CLI (método rápido)

```bash
paddleocr ocr -i https://paddle-model-ecology.bj.bcebos.com/paddlex/imgs/demo_image/general_ocr_002.png \
    --save_path ./output --device cpu
```

### 5.2 Script Python (validado contra la documentación oficial)

```python
import json
from paddleocr import PaddleOCR

# 1. Inicializar el pipeline (la primera ejecución descargará los modelos)
ocr = PaddleOCR(device="cpu")  # Alternativas: lang="en", ocr_version="PP-OCRv5"

# 2. Ejecutar la predicción (acepta imagen, directorio, PDF o URL)
resultados = ocr.predict("ruta/a/tu/imagen.png")  # Lista de objetos Result

# 3. Verificar que la lista no esté vacía
if not resultados:
    raise RuntimeError("No se obtuvo ningún resultado del modelo. Verifica la imagen.")

# 4. Guardar JSON completo para depuración
res = resultados[0]
with open("ocr_salida_bruta.json", "w", encoding="utf-8") as f:
    json.dump(res.json, f, indent=2, ensure_ascii=False)

# 5. Extraer los textos reconocidos (estructura oficial: res.rec_texts)
info = res.json["res"]
textos = info.get("rec_texts", [])          # List[str] con los textos reconocidos
scores = info.get("rec_scores", [])         # Confianza por texto
poligonos = info.get("rec_polys", [])       # Cajas (polígonos) de cada texto

# 6. Mostrar y guardar
for texto, score in zip(textos, scores):
    print(f"[{score:.3f}] {texto}")

with open("ocr_texto_extraido.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(textos))

print(f"\n[OK] {len(textos)} textos reconocidos guardados en 'ocr_texto_extraido.txt'")
```

### 5.3 Estructura oficial de salida

`res.json["res"]` contiene (según la documentación oficial):

| Clave | Contenido |
| :--- | :--- |
| `input_path` | Ruta de la imagen de entrada |
| `dt_polys` | Lista de cajas de detección (polígonos de 4 vértices) |
| `rec_texts` | **Lista de textos reconocidos** (solo confianza > umbral) |
| `rec_scores` | Confianza de cada texto |
| `rec_polys` | Cajas de cada texto reconocido |
| `text_det_params` | Parámetros de detección utilizados |
| `doc_preprocessor_res` | Resultado del preprocesado (orientación/enderezado) |

### 5.4 Especificaciones (datos oficiales)

| Parámetro | Valor |
| :--- | :--- |
| **Modelos por defecto** | PP-OCRv6_medium_det (59.4 MB) + PP-OCRv6_medium_rec (73.3 MB) ≈ **133 MB** en total |
| **Idiomas** | 50 idiomas con un solo modelo |
| **Detección (oficial)** | Hmean 86.2* (PP-OCRv6_medium_det) |
| **Reconocimiento (oficial)** | 83.2* (PP-OCRv6_medium_rec) |
| **CPU detección (PP-OCRv5_server, oficial)** | 383 ms/imagen (estándar, 8 hilos) |
| **CPU reconocimiento (PP-OCRv5_server, oficial)** | 31 ms/línea (estándar, 8 hilos) |
| **Rendimiento en este entorno** | **No medido** — los modelos son ~17× más ligeros que PP-Chart2Table; esperable segundos por página en CPU |

*Métricas PP-OCRv6 sobre conjunto de evaluación interno multi-escenario; no comparables directamente con PP-OCRv5/v4.*

---

## 6. AI VISION — IMAGEN + PREGUNTA → RESPUESTA (PP-DocBee)

Entendimiento visual general mediante un VLM: dado cualquier tipo de imagen (foto, documento, diagrama, captura) y una pregunta en lenguaje natural, el modelo responde describiendo, extrayendo o razonando sobre el contenido. El pipeline `doc_understanding` incluye los modelos **PP-DocBee-2B** y **PP-DocBee2-3B** (familia PP-DocBee).

### 6.1 CLI (método rápido)

```bash
paddleocr doc_understanding -i "{'image': 'https://paddle-model-ecology.bj.bcebos.com/paddlex/imgs/demo_image/medal_table.png', 'query': 'Identifica el contenido de esta imagen'}"
```

### 6.2 Script Python

```python
import json
from paddleocr import DocUnderstanding

# 1. Inicializar el pipeline (la primera ejecución descargará el modelo)
pipeline = DocUnderstanding(device="cpu")  # device explícito: el default prioriza GPU

# 2. Ejecutar la predicción: imagen + pregunta en lenguaje natural
output = pipeline.predict({
    "image": "ruta/a/tu/imagen.png",
    "query": "Describe el contenido de esta imagen y extrae los datos principales",
})

# 3. Verificar que la lista no esté vacía
if not output:
    raise RuntimeError("No se obtuvo ningún resultado del modelo. Verifica la imagen.")

res = output[0]

# 4. Guardar JSON completo para depuración (usando .json para serializar)
with open("vision_salida_bruta.json", "w", encoding="utf-8") as f:
    json.dump(res.json, f, indent=2, ensure_ascii=False)

# 5. Acceso robusto a la respuesta: 'result' en raíz o dentro de 'res'
respuesta = None
if "result" in res.json:
    respuesta = res.json["result"]
elif "res" in res.json and "result" in res.json["res"]:
    respuesta = res.json["res"]["result"]

if not respuesta:
    print("Estructura JSON recibida:", json.dumps(res.json, indent=2))
    raise KeyError("No se encontró la clave 'result'. Revisa vision_salida_bruta.json.")

# 6. Mostrar y guardar la respuesta
print("Respuesta del modelo:\n")
print(respuesta)

with open("vision_respuesta.txt", "w", encoding="utf-8") as f:
    f.write(respuesta)

print("\n[OK] Respuesta guardada en 'vision_respuesta.txt'")
```

### 6.3 Estructura oficial de salida

```text
{'res': {'image': 'ruta/a/tu/imagen.png',
         'query': 'Descripción de la pregunta',
         'result': 'Respuesta del modelo en lenguaje natural'}}
```

### 6.4 Especificaciones (datos oficiales)

| Parámetro | Valor |
| :--- | :--- |
| **Modelos disponibles** | PP-DocBee-2B (4.2 GB) · PP-DocBee2-3B (7.6 GB) |
| **Puntuación (oficial)** | 765 (PP-DocBee-2B) · 852 (PP-DocBee2-3B) |
| **Evaluación (oficial)** | 1196 muestras internas (reportes financieros, leyes, papers, contratos, etc.), resolución (1680, 1204) |
| **Rendimiento en este entorno** | **No medido** — requiere previsión de RAM (modelo 2B ≈ 4.2 GB en disco; RAM no documentada) |
| **Fine-tuning** | No soportado actualmente (solo inferencia), igual que PP-Chart2Table |

---

## 7. LECCIONES OPERACIONALES CLAVE (TRANSVERSALES A LOS TRES PIPELINES)

Las siguientes lecciones han sido extraídas de pruebas en un entorno de producción con PP-Chart2Table y son **críticas** para un despliegue estable. **No están documentadas oficialmente** (el FAQ del módulo está vacío), por lo que se consideran hallazgos empíricos de este proyecto.

### 7.1. Una sola instancia del modelo por máquina

Intentar cargar dos instancias de un modelo VLM simultáneamente (ej. un proceso daemon + un segundo proceso) provocará un **OOM kill** real (confirmado por `dmesg` con consumo de 7.6 GB sobre 7.7 GB disponibles, en una máquina de 8 GB de RAM).

**Regla de oro:** **Nunca** ejecutes más de una instancia de un modelo VLM (`ChartParsing`, `DocUnderstanding`) por máquina.

### 7.2. PaddleX NO es thread-safe

La ejecución de `predict()` desde múltiples hilos de forma concurrente provoca fallos del tipo `uint8 in assign` y crasheos del proceso. **La inferencia debe serializarse** (ej. mediante una cola de tareas o un servidor que procese una petición a la vez).

### 7.3. Patrón recomendado: Daemon persistente

En lugar de una "cola de tareas" genérica, se recomienda un **daemon persistente** que:

- Carga el modelo una única vez (evitando el pico de RAM en cada carga).
- Permanece residente en memoria.
- Implementa auto-unload tras periodos de inactividad.
- Evita los ~95 segundos de inicialización en cada ejecución.

**Implementación de referencia en este proyecto:** `chart_server.py` — servidor HTTP de un solo hilo (serializa la inferencia por construcción) que **se cierra solo tras 1 hora sin peticiones de inferencia** (configurable con `--timeout`), por lo que no queda ningún proceso en memoria permanentemente. Las peticiones de *health check* no reinician el temporizador (un monitor no mantiene vivo el proceso). Endpoints: `POST /chart` (JSON `{"image": "..."}` → `markdown` + `csv`) y `GET /health`. Si además se quiere limitar la vida total del proceso, envolver con `timeout 3600 python chart_server.py`.

---

## 8. RECOMENDACIONES PARA ENTORNOS DE PRODUCCIÓN

1.  **Verificación previa del tipo de gráfico (Chart OCR):** Si tu caso de uso involucra principalmente **líneas**, ejecuta una prueba con una muestra representativa antes de integrar el script en un pipeline. El modelo falló en la detección de la línea en la prueba realizada.
2.  **Redimensionamiento de imágenes:** Aunque no fue probado formalmente, reducir imágenes de resolución muy alta (>4K) a 1920x1080 puede disminuir significativamente el tiempo de inferencia y el consumo de RAM de los modelos VLM.
3.  **Gestión de recursos:**
    - Asegura al menos **3 GB libres** en el directorio de descarga (`TMPDIR`).
    - Provisiona al menos **8 GB de RAM** para modelos VLM (PP-Chart2Table mide 4.8 GB de pico en carga; PP-DocBee es mayor).
    - **Nunca** ejecutes más de una instancia de un modelo VLM por máquina.
    - Text OCR es ligero (~133 MB) y no requiere estas previsiones.
4.  **Arquitectura de servicio:**
    - Implementa un **daemon persistente** que cargue el modelo una sola vez.
    - Serializa todas las peticiones de inferencia (PaddleX no es thread-safe).
    - Considera un mecanismo de auto-unload tras inactividad para liberar recursos.
5.  **Inspección de salidas:** Antes de confiar en los resultados, inspecciona siempre los JSON crudos generados (`salida_bruta.json`, `ocr_salida_bruta.json`, `vision_salida_bruta.json`) para verificar que la estructura coincide con tus expectativas.

---

## 9. CONCLUSIÓN FINAL

**PP‑Chart2Table** demuestra ser una herramienta funcional y precisa para la extracción de datos en gráficos de barras en las condiciones específicas probadas (CPU, Linux, Python 3.12). Sin embargo, **su rendimiento en gráficos mixtos (especialmente con líneas) no está garantizado** y requiere validación adicional por parte del usuario final.

**Text OCR (PP-OCRv6)** es el más maduro y ligero de los tres pipelines: modelos de ~133 MB, multilingüe, con API sencilla y salida estructurada (`rec_texts`/`rec_scores`). No fue medido en este entorno, pero los datos oficiales indican tiempos de segundos por página en CPU.

**AI Vision (PP-DocBee)** ofrece entendimiento visual general (imagen + pregunta → respuesta) con el mismo patrón de código y las mismas advertencias operativas que Chart OCR, a costa de mayor peso en disco y RAM.

Las lecciones operativas documentadas en la Sección 7 (una instancia, no thread-safe, daemon persistente) son **críticas** para cualquier despliegue en producción de los pipelines VLM y representan el valor añadido más significativo de esta guía.

---

*Fin del documento.*

---

## 10. FLUJO GPU + TESTS E2E CON VISIÓN IA LOCAL (2026-08-01)

Complemento a la guía (que es CPU): verificación de UI con visión local en
GPU (8 GB) desde un e2e web, tal como se aplica en visorweb2
(`test-e2e/vision_analyze.py` + `test-e2e/vision-e2e.mjs`).

### 10.1 Pipeline por tarea

| Tarea | Modelo | Uso en e2e |
|---|---|---|
| `ocr` / `descripcion` | PP-OCRv6 medium (det+rec) | texto (con posición) de cada pantallazo |
| `vision` | PP-DocBee-2B | respuesta a una pregunta sobre la captura (QA visual) |
| `yolo` / `blip` / `opencv` | opcional | objetos / leyenda / métricas (error controlado si el venv no los tiene) |

Contrato JSON: `{text, confidence, bbox}` (OCR) e `{items: [{text, score, poly}]}`
(descripcion). El LLM de texto recibe estos JSON y razona sobre la UI real.

### 10.2 Cuellos de botella GPU resueltos

1. **PP-DocBee-2B**: `cu_seqlens` debe ser `int32` — castear
   `attention_mask`/`position_ids` con `.astype('int32')`; redimensionar la
   imagen a **512 px** antes de la predicción.
2. **cuDNN**: resolver `libcudnn.so.8` → `libcudnn.so.9` para los bins del
   venv (los enlaces se colocan dentro del venv, no en el sistema).
3. **LD_LIBRARY_PATH**: para correr fuera del venv activo (e2e/subprocesos),
   incluir `paddle/libs` + `nvidia/*/lib` del venv.
4. **In-process aborts (SIGABRT)**: cargar PaddleOCR y procesar capturas en
   el mismo proceso de unittest aborta (cuDNN); los tests con capturas reales
   deben correr en subproceso.
5. **Memoria**: DocBee 8 GB VRAM es suficiente con las previsiones de la
   Sección 8 (una instancia, serializado).

### 10.3 Contexto de cada pantallazo (obligatorio)

En el e2e, cada captura registra: **origen** (viewport headless de
Playwright), **dimensiones** (viewport e imagen), **qué se ve de frente**
(la app web) y **de fondo** (ninguno en headless), **pageUrl**, y el
**entorno del sistema** (pantallas vía `xrandr`, escritorios virtuales vía
`wmctrl -d`, sesión gráfica) — sin hostnames ni datos personales (P0.9).
Con múltiples escritorios virtuales, las pruebas se hacen en un escritorio
limpio y aislado; las capturas headless no tocan el escritorio del usuario.

### 10.4 Verificación

- Suite de tests del analizador: `paddle-venv/bin/python -m unittest test_vision_analyze` (46/46 en visorweb2).
- E2e: `node vision-e2e.mjs` (OCR estructural + descripcion + QA visual por estado), con `VISION_E2E_SKIP_HEAVY=1` para un pase rápido.

*Fin del documento.*
