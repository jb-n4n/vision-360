#!/usr/bin/env python3
"""Batería 360°: compara motores VLM (docbee vs ollama/qwen2.5vl) en 6
dimensiones con las mismas imágenes, mismos prompts y misma temperatura.

Dimensiones y scoring:
  ui_qa           : QA de UI (campo/elemento esperado)        -> etiquetas
  interpretacion  : interpretación de gráficos (free-form)    -> rúbrica humana
  valores         : lectura de valores exactos                -> n/N numérico
  objetos         : identificación de objetos                 -> etiquetas + conteo
  descripcion     : descripción avanzada (free-form)          -> rúbrica humana
  documento       : lectura de documento                      -> etiquetas

Motores:
  docbee  : PaddleOCR DocVLM (PP-DocBee-2B) — subproceso aislado con
            medición de tiempo y RAM pico. GPU (lección 17): requiere
            --device cuda (se normaliza a 'gpu'), parchea cu_seqlens a
            int32 (paddle 3.3.1 GPU), max_pixels 0.5M px en 8 GB y
            LD_LIBRARY_PATH con los nvidia-* del venv por delante.
  ollama  : API local ollama (gemma3:4b) — HTTP {host}:11434

Uso (en la máquina con GPU/ollama):
  python scripts/bateria_360.py --motor ambos
  python scripts/bateria_360.py --motor docbee --solo valores_demo
  python scripts/bateria_360.py --motor ollama --host 127.0.0.1

Las dimensiones libres (interpretacion/descripcion) se guardan crudas en
/var/tmp/bateria360/ para rúbrica humana; las objetivas se puntúan solas.
"""

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIR_REPORTE = "/var/tmp/bateria360"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = lambda p: os.path.join(RAIZ, p)  # noqa: E731

# Valores de referencia del gráfico oficial (docs PaddleOCR, chart_parsing_02)
VALORES_DEMO = [104.22, 99.11, 57.87, -3.87, 68.99, -2.9, 56.29, -9.48, 87.99, 5.96, 9.87, 7.47]
VALORES_PIE = [35.0, 25.5, 18.2, 13.3, 8.0]

BATERIA = [
    {
        "id": "ui_qa",
        "dimension": "ui_qa",
        "imagen": os.environ.get("BATERIA_UI_IMAGEN", IMG("ejemplos/test_charts/texto_boarding.png")),
        "prompt": "Lista los campos y valores visibles de este documento. "
                  "Formato: campo: valor, uno por linea.",
        "esperado": {"etiquetas": ["flight", "gate", "seat", "name"]},
        "scoring": "etiquetas",
    },
    {
        "id": "interpretacion",
        "dimension": "interpretacion",
        "imagen": IMG("ejemplos/test_charts/bar_2series.png"),
        "prompt": "Describe en 3 frases la tendencia de ingresos y beneficios por año.",
        "esperado": {},
        "scoring": "libre",
    },
    {
        "id": "valores_demo",
        "dimension": "valores",
        "imagen": IMG("ejemplos/grafico_demo.png"),
        "prompt": "Extrae los valores del grafico: para cada año, ingresos y beneficios. "
                  "Responde solo numeros separados por coma, sin texto.",
        "esperado": {"valores": VALORES_DEMO},
        "scoring": "valores",
    },
    {
        "id": "valores_pie",
        "dimension": "valores",
        "imagen": IMG("ejemplos/test_charts/pie_5.png"),
        "prompt": "Extrae el valor numerico de cada sector del grafico circular. "
                  "Responde solo numeros separados por coma, sin texto.",
        "esperado": {"valores": VALORES_PIE},
        "scoring": "valores",
    },
    {
        "id": "objetos_frutas",
        "dimension": "objetos",
        "imagen": IMG("ejemplos/test_charts/foto_det.png"),
        "prompt": "List the objects you see in the image, one per line (answer in English).",
        "esperado": {"etiquetas": ["banana", "apple", "orange"], "conteo": 3},
        "scoring": "objetos",
    },
    {
        "id": "objetos_personas",
        "dimension": "objetos",
        "imagen": IMG("ejemplos/test_charts/foto_personas.jpg"),
        "prompt": "Cuantas personas hay en la imagen? Responde solo el numero.",
        "esperado": {"etiquetas": ["person"], "conteo": 10},
        "scoring": "objetos",
    },
    {
        "id": "descripcion",
        "dimension": "descripcion",
        "imagen": IMG("ejemplos/test_charts/foto_det.png"),
        "prompt": "Describe la imagen con detalle: contenido, colores, composicion.",
        "esperado": {},
        "scoring": "libre",
    },
    {
        "id": "documento",
        "dimension": "documento",
        "imagen": IMG("ejemplos/test_charts/doc_demo.png"),
        "prompt": "Resume este documento: tipo, titulo, secciones y figuras.",
        "esperado": {"etiquetas": ["RT-DETR", "Figure"]},
        "scoring": "etiquetas",
    },
]


# --- Puntuadores puros (testeables sin motores) --------------------------------

def extraer_numeros(texto: str) -> list[float]:
    """Todos los floats plausibles del texto (tolera '%' y comas decimales)."""
    numeros = []
    for tok in re.findall(r"-?\d+(?:[.,]\d+)?%?", texto):
        try:
            numeros.append(float(tok.replace(",", ".").rstrip("%")))
        except ValueError:
            continue
    return numeros


def puntuar_valores(texto: str, esperados: list[float], tolerancia=0.01) -> tuple[int, int]:
    hallados = extraer_numeros(texto)
    aciertos = sum(1 for e in esperados if any(abs(h - e) <= tolerancia for h in hallados))
    return aciertos, len(esperados)


def puntuar_etiquetas(texto: str, esperadas: list[str]) -> tuple[int, int]:
    bajo = texto.lower()
    aciertos = sum(1 for e in esperadas if e.lower() in bajo)
    return aciertos, len(esperadas)


def puntuar_conteo(texto: str, clase: str, esperado: int, tolerancia_frac=0.2) -> tuple[int, int]:
    numeros = extraer_numeros(texto)
    if not numeros:
        return 0, 1
    hallado = int(round(numeros[0]))
    return (1, 1) if abs(hallado - esperado) <= max(1, esperado * tolerancia_frac) else (0, 1)


def puntuar(test: dict, texto: str) -> dict:
    """Devuelve {aciertos, total} o {error} para scoring libre."""
    tipo = test["scoring"]
    esperado = test.get("esperado", {})
    if tipo == "valores":
        a, t = puntuar_valores(texto, esperado["valores"])
        return {"aciertos": a, "total": t, "tipo": "valores"}
    if tipo == "etiquetas":
        a, t = puntuar_etiquetas(texto, esperado["etiquetas"])
        return {"aciertos": a, "total": t, "tipo": "etiquetas"}
    if tipo == "objetos":
        a, t = puntuar_etiquetas(texto, esperado["etiquetas"])
        c, tc = puntuar_conteo(texto, esperado["etiquetas"][0], esperado["conteo"])
        return {"aciertos": a + c, "total": t + tc, "tipo": "objetos+conteo"}
    return {"tipo": "libre", "nota": "requiere rubrica humana"}


# --- Adaptadores de motor ------------------------------------------------------

def run_docbee(imagen: str, prompt: str, device: str, timeout_s=1800) -> dict:
    """DocVLM (PP-DocBee-2B) en subproceso aislado con RAM pico."""
    # paddlex espera 'gpu', no 'cuda' (SUPPORTED_DEVICE_TYPE en paddlex/utils/device.py)
    device = "gpu" if device.startswith("cuda") else device
    # Workaround GPU (paddle 3.3.1 + paddlex 3.7.2, ver docs/LECCIONES-APRENDIDAS):
    # paddle.cumsum promueve int32 -> int64 y flash_attn_unpadded exige int32
    # (crashea con SIGABRT/InvalidArgument). Se fuerza int32 en _get_unpad_data.
    patch_int32 = """
from paddlex.inference.models.doc_vlm.modeling import qwen2_vl as _qm
_orig_unpad = _qm._get_unpad_data
def _fix_unpad(mask):
    indices, cu, mx = _orig_unpad(mask)
    return indices, cu.astype('int32'), mx
_qm._get_unpad_data = _fix_unpad
from paddlex.inference.models.doc_vlm.processors import qwen2_vl as _pq
_pq.MAX_PIXELS = 262144
"""
    codigo = f"""
import json, sys, time
sys.path.insert(0, {RAIZ!r})
{patch_int32}
from paddleocr import DocVLM
modelo = DocVLM(model_name="PP-DocBee-2B", device={device!r})
t0 = time.monotonic()
res = modelo.predict({{"image": {imagen!r}, "query": {prompt!r}}})
texto = ""
for r in res:
    texto += (r.json.get("res", {{}}).get("result") or "") + "\\n"
pico = 0.0
try:
    with open("/proc/self/status") as f:
        for l in f:
            if l.startswith("VmHWM:"):
                pico = int(l.split()[1]) / 1024
except OSError:
    pass
print(json.dumps({{"texto": texto.strip(), "tiempo_s": round(time.monotonic()-t0, 1), "ram_mb": round(pico, 1)}}))
"""
    t0 = time.monotonic()
    env = {**os.environ, "TMPDIR": "/var/tmp",
           "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": "0"}
    # GPU: las nvidia-*.cu12 del venv (CUDNN 9.5 etc.) deben cargarse ANTES que
    # cualquier nvidia-cu12 del sistema/pyenv (CUDNN 9.1 rompe paddle con
    # libcudnn_graph.so: undefined symbol).
    import site as _site
    nvidia_dirs = sorted(
        glob.glob(os.path.join(_site.getsitepackages()[0], "nvidia", "*", "lib"))
    )
    ld = ":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":")
                  if p and ".pyenv" not in p)
    env["LD_LIBRARY_PATH"] = ":".join(nvidia_dirs + [ld])
    try:
        proc = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout {timeout_s}s"}
    try:
        salida = json.loads(proc.stdout.strip().splitlines()[-1])
        salida["ok"] = True
        salida["total_s"] = round(time.monotonic() - t0, 1)
        return salida
    except (json.JSONDecodeError, IndexError) as e:
        return {"ok": False, "error": f"salida invalida: {e}",
                "stderr": proc.stderr[-300:]}


def _garantizar_ollama(host: str, binario: str | None = None) -> tuple[bool, str]:
    """Arranca el servidor ollama bajo demanda si no responde (no servicio
    permanente). Devuelve (ok, detalle)."""
    import socket

    try:
        with socket.create_connection((host, 11434), timeout=2):
            return True, "ollama ya activo"
    except OSError:
        pass
    binario = binario or os.path.expanduser("~/ollama/bin/ollama")
    if not os.path.exists(binario):
        import shutil
        binario = shutil.which("ollama") or binario
    if not os.path.exists(binario):
        return False, f"no se encuentra el binario de ollama: {binario}"
    import subprocess
    try:
        subprocess.Popen([binario, "serve"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        return False, f"no se pudo arrancar ollama: {e}"
    for _ in range(30):
        try:
            with socket.create_connection((host, 11434), timeout=2):
                return True, "ollama arrancado bajo demanda"
        except OSError:
            time.sleep(2)
    return False, "ollama no respondio en 60 s"


def run_ollama(imagen: str, prompt: str, host: str, modelo="qwen2.5vl:3b",
               timeout_s=1800) -> dict:
    """API /api/generate de ollama (temperatura 0 = determinista).
    Arranca el servidor bajo demanda si no esta escuchando."""
    ok, detalle = _garantizar_ollama(host)
    if not ok:
        return {"ok": False, "error": detalle}
    with open(imagen, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    cuerpo = json.dumps({
        "model": modelo, "prompt": prompt, "images": [b64], "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(f"http://{host}:11434/api/generate",
                                 data=cuerpo, headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            datos = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 — red/API/JSON
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "texto": datos.get("response", ""),
            "tiempo_s": round(time.monotonic() - t0, 1),
            "total_s": round(time.monotonic() - t0, 1), "ram_mb": None}


# --- Orquestador -----------------------------------------------------------------

def ejecutar_motor(motor: str, test: dict, host: str, device: str, modelo: str) -> dict:
    imagen, prompt = test["imagen"], test["prompt"]
    if not os.path.exists(imagen):
        return {"ok": False, "error": f"imagen inexistente: {imagen}",
                "motor": motor, "id": test["id"]}
    if motor == "docbee":
        resultado = run_docbee(imagen, prompt, device)
    elif motor == "ollama":
        resultado = run_ollama(imagen, prompt, host, modelo)
    else:
        return {"ok": False, "error": f"motor desconocido: {motor}"}
    resultado["motor"] = motor
    resultado["id"] = test["id"]
    resultado["dimension"] = test["dimension"]
    if resultado.get("ok"):
        resultado["puntaje"] = puntuar(test, resultado["texto"])
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(description="Bateria 360° VLM (docbee vs qwen2.5vl)")
    parser.add_argument("--motor", choices=["docbee", "ollama", "ambos"], default="ambos")
    parser.add_argument("--host", default="127.0.0.1", help="host de ollama (default: 127.0.0.1)")
    parser.add_argument("--modelo", default="gemma3:4b", help="modelo ollama (default: gemma3:4b)")
    parser.add_argument("--device", default="cuda", help="device para docbee (default: cuda)")
    parser.add_argument("--solo", default=None, help="id de test unico (ej. valores_demo)")
    args = parser.parse_args()

    motores = ["docbee", "ollama"] if args.motor == "ambos" else [args.motor]
    tests = [t for t in BATERIA if args.solo is None or t["id"] == args.solo]
    os.makedirs(DIR_REPORTE, exist_ok=True)

    informe = {"motor": args.motor, "host_ollama": args.host, "device": args.device, "tests": []}
    print(f"{'Motor':<8}{'Test':<16}{'Dimension':<14}{'Puntaje':<14}{'Tiempo':<10}{'RAM':<8}  Estado")
    print("-" * 78)
    for test in tests:
        for motor in motores:
            r = ejecutar_motor(motor, test, args.host, args.device, args.modelo)
            informe["tests"].append(r)
            if not r.get("ok"):
                print(f"{motor:<8}{test['id']:<16}{test['dimension']:<14}{'—':<14}{'—':<10}{'—':<8}  FALLO: {r.get('error','')[:50]}")
                continue
            p = r["puntaje"]
            if p.get("tipo") == "libre":
                punt = "rubrica"
            else:
                punt = f"{p['aciertos']}/{p['total']}"
            print(f"{motor:<8}{test['id']:<16}{test['dimension']:<14}{punt:<14}"
                  f"{r['tiempo_s']:<10}{str(r.get('ram_mb','')):<8}  OK")
            # guardar salida cruda para rubrica humana de los libres
            with open(os.path.join(DIR_REPORTE, f"{motor}_{test['id']}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(r.get("texto", ""))
    ruta = os.path.join(DIR_REPORTE, "reporte.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(informe, f, indent=2, ensure_ascii=False)
    print(f"\nInforme y salidas crudas: {DIR_REPORTE}/")


if __name__ == "__main__":
    main()
