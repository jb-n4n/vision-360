#!/usr/bin/env python3
"""Benchmark de motores OCR/parseo de gráficos sobre una misma imagen.

Compara 4 motores de PaddleOCR 3.7.0 (Apache 2.0) sobre una imagen de
gráfico de barras, midiendo tiempo de carga, tiempo de inferencia y
memoria pico (RSS), y puntúa la extracción contra la salida de referencia
del modelo oficial PP-Chart2Table (documentación PaddleOCR, imagen
chart_parsing_02.png):

    年份 | 单家五星级旅游饭店年平均营收 (百万元) | 单家五星级旅游饭店年平均利润 (百万元)
    2018 | 104.22 | 9.87
    2019 | 99.11 | 7.47
    2020 | 57.87 | -3.87
    2021 | 68.99 | -2.9
    2022 | 56.29 | -9.48
    2023 | 87.99 | 5.96

Fuente: https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/chart_parsing.md

Motores:
- chart_parsing : ChartParsing (PP-Chart2Table) — línea base actual del proyecto
- structure_v3  : PP-StructureV3 con solo chart_recognition activado
- ocr_v6        : PaddleOCR PP-OCRv6 (OCR de texto; sin reconstrucción de tabla)
- ocr_v5        : PaddleOCR PP-OCRv5 (ídem)

Cada motor se ejecuta en un subproceso aislado (un solo modelo VLM a la vez,
PaddleX no es thread-safe) y reporta un JSON por stdout. El modo padre
orquesta, puntúa y escribe el informe en /var/tmp/better-ocr-bench/.

Uso:
    python scripts/benchmark_ocr.py [--solo ocr_v6] [--imagen ejemplos/grafico_demo.png]
    python scripts/benchmark_ocr.py run --engine <nombre> --imagen <ruta>  (interno)

Ejecutar con el venv del proyecto: /home/admin/venvs/paddle312/bin/python
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor_final import es_fila_separadora, markdown_a_df, obtener_markdown  # noqa: E402

IMAGEN_DEFECTO = "ejemplos/grafico_demo.png"
DIR_REPORTE = "/var/tmp/better-ocr-bench"

ANIOS = ["2018", "2019", "2020", "2021", "2022", "2023"]
INGRESOS = [104.22, 99.11, 57.87, 68.99, 56.29, 87.99]
BENEFICIOS = [9.87, 7.47, -3.87, -2.9, -9.48, 5.96]
VALORES_NUMERICOS = INGRESOS + BENEFICIOS  # 12 valores esperados

INGRESOS_GT = dict(zip(ANIOS, INGRESOS))
BENEFICIOS_GT = dict(zip(ANIOS, BENEFICIOS))

MOTORES = ["chart_parsing", "structure_v3", "ocr_v6", "ocr_v5"]


@dataclass
class Resultado:
    engine: str
    ok: bool = False
    error: str = ""
    load_s: float = 0.0
    infer_s: float = 0.0
    rss_mb: float = 0.0
    markdown: str = ""
    textos: list = field(default_factory=list)
    puntaje: dict = field(default_factory=dict)


def rss_pico_mb() -> float:
    """Memoria pico del proceso (VmHWM) en MB."""
    try:
        with open("/proc/self/status") as f:
            for linea in f:
                if linea.startswith("VmHWM:"):
                    return int(linea.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def extraer_tabla_markdown(texto: str) -> str:
    """Devuelve el bloque de líneas con pipes de un texto (primera tabla)."""
    lineas_tabla = []
    for linea in texto.splitlines():
        if "|" in linea or es_fila_separadora(linea):
            lineas_tabla.append(linea)
    return "\n".join(lineas_tabla).strip()


def puntuar_tabla(df, resultado: Resultado) -> None:
    """Compara un DataFrame extraído contra la referencia oficial."""
    if df is None or df.shape[0] == 0:
        resultado.puntaje = {"error": "tabla vacía"}
        return
    df = df.astype(str)
    anio_col = df.iloc[:, 0].str.strip()
    aciertos_anios = sum(1 for v in ANIOS if (anio_col == v).any())
    aciertos_ingresos = 0
    aciertos_beneficios = 0
    for _, fila in df.iterrows():
        anio = fila.iloc[0].strip()
        if anio not in INGRESOS_GT:
            continue
        for col in range(1, min(3, len(fila))):
            try:
                valor = round(float(fila.iloc[col].replace(",", ".")), 2)
            except ValueError:
                continue
            if col == 1 and valor == INGRESOS_GT[anio]:
                aciertos_ingresos += 1
            if col == 2 and valor == BENEFICIOS_GT[anio]:
                aciertos_beneficios += 1
    resultado.puntaje = {
        "anios": f"{aciertos_anios}/6",
        "ingresos": f"{aciertos_ingresos}/6",
        "beneficios": f"{aciertos_beneficios}/6",
        "celdas": f"{aciertos_anios + aciertos_ingresos + aciertos_beneficios}/18",
    }


def puntuar_ocr(textos: list, resultado: Resultado) -> None:
    """Cuenta cuántos valores de referencia aparecen en el texto OCR."""
    tokens = []
    for t in textos:
        tokens += re.findall(r"-?\d+(?:\.\d+)?", t)

    def numeros():
        for tok in tokens:
            try:
                yield float(tok)
            except ValueError:
                continue

    numeros_ocr = list(numeros())
    hallados_numeros = 0
    for gt in VALORES_NUMERICOS:
        if any(abs(n - gt) <= 0.01 for n in numeros_ocr):
            hallados_numeros += 1
    hallados_anios = sum(1 for a in ANIOS if a in tokens)
    resultado.puntaje = {
        "anios_ocr": f"{hallados_anios}/6",
        "valores_ocr": f"{hallados_numeros}/12",
    }


def run_engine(engine: str, imagen: str) -> Resultado:
    """Carga el modelo y predice (modo hijo, import perezoso de paddle)."""
    r = Resultado(engine=engine)

    if engine == "chart_parsing":
        t0 = time.monotonic()
        from paddleocr import ChartParsing

        model = ChartParsing(device="cpu")
        r.load_s = round(time.monotonic() - t0, 2)
        t1 = time.monotonic()
        res = model.predict({"image": imagen})
        r.infer_s = round(time.monotonic() - t1, 2)
        r.markdown = obtener_markdown(res[0])

    elif engine == "structure_v3":
        t0 = time.monotonic()
        from paddleocr import PPStructureV3

        model = PPStructureV3(
            device="cpu",
            enable_mkldnn=False,  # bug paddlepaddle 3.3.1 (PIR + oneDNN), issue #18162
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            use_table_recognition=False,
            use_formula_recognition=False,
            use_seal_recognition=False,
            use_region_detection=False,
            use_chart_recognition=True,
        )
        r.load_s = round(time.monotonic() - t0, 2)
        t1 = time.monotonic()
        res = model.predict(imagen)  # PPStructureV3 acepta str/ndarray, no dict
        r.infer_s = round(time.monotonic() - t1, 2)
        r.markdown = extraer_tabla_markdown(
            res[0].markdown.get("markdown_texts", "")
        )

    elif engine in ("ocr_v6", "ocr_v5"):
        t0 = time.monotonic()
        from paddleocr import PaddleOCR

        # enable_mkldnn=False: bug confirmado de paddlepaddle 3.3.1 (PIR + oneDNN)
        # rompe PP-OCRv6 (issue PaddlePaddle/PaddleOCR#18162); workaround oficial.
        model = PaddleOCR(device="cpu", enable_mkldnn=False,
                          ocr_version="PP-OCRv6" if engine == "ocr_v6" else "PP-OCRv5")
        r.load_s = round(time.monotonic() - t0, 2)
        t1 = time.monotonic()
        res = model.predict(imagen)
        r.infer_s = round(time.monotonic() - t1, 2)
        r.textos = list(res[0].json.get("res", {}).get("rec_texts", []))

    else:
        raise ValueError(f"Motor desconocido: {engine}")

    r.rss_mb = round(rss_pico_mb(), 1)
    r.ok = True
    return r


def modo_hijo(engine: str, imagen: str) -> None:
    """Modo subproceso: imprime un JSON por stdout con el resultado."""
    try:
        r = run_engine(engine, imagen)
        if r.puntaje:
            pass
    except Exception as e:  # noqa: BLE001 — el hijo debe reportar el fallo al padre
        r = Resultado(engine=engine, ok=False, error=f"{type(e).__name__}: {e}")
    if r.textos:
        r.markdown = ""
    print(json.dumps({
        "engine": r.engine,
        "ok": r.ok,
        "error": r.error,
        "load_s": r.load_s,
        "infer_s": r.infer_s,
        "rss_mb": r.rss_mb,
        "markdown": r.markdown,
        "textos": r.textos,
    }, ensure_ascii=False))


def puntuar(resultado: Resultado, salida: dict) -> None:
    resultado.ok = salida["ok"]
    resultado.error = salida.get("error", "")
    resultado.load_s = salida.get("load_s", 0.0)
    resultado.infer_s = salida.get("infer_s", 0.0)
    resultado.rss_mb = salida.get("rss_mb", 0.0)
    resultado.markdown = salida.get("markdown", "")
    resultado.textos = salida.get("textos", [])
    if not resultado.ok:
        return
    if resultado.markdown:
        try:
            puntuar_tabla(markdown_a_df(resultado.markdown), resultado)
        except (ValueError, KeyError) as e:
            resultado.puntaje = {"error": f"{type(e).__name__}: {e}"}
    else:
        puntuar_ocr(resultado.textos, resultado)


def modo_padre(imagen: str, solo: str | None) -> None:
    os.makedirs(DIR_REPORTE, exist_ok=True)
    motores = [solo] if solo else MOTORES
    print(f"Benchmark de {len(motores)} motores sobre: {imagen}")
    print("Aviso: la primera ejecución descarga modelos nuevos (PP-OCRv5/v6,")
    print("módulos PP-StructureV3); cada motor se ejecuta en subproceso aislado.")
    print("Tiempo estimado total: 10-25 min en CPU.\n")

    resultados: dict[str, Resultado] = {}
    for engine in motores:
        print(f"[{engine}] ejecutando...", flush=True)
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "run",
                 "--engine", engine, "--imagen", imagen],
                capture_output=True, text=True, timeout=1500,
                env={**os.environ, "TMPDIR": "/var/tmp"},
            )
        except subprocess.TimeoutExpired:
            resultados[engine] = Resultado(engine=engine, ok=False,
                                           error="timeout 1500 s")
            print(f"[{engine}] TIMEOUT\n")
            continue
        t_total = round(time.monotonic() - t0, 2)
        resultado = Resultado(engine=engine)
        try:
            salida = json.loads(proc.stdout.strip().splitlines()[-1])
            puntuar(resultado, salida)
        except (json.JSONDecodeError, IndexError) as e:
            resultado.ok = False
            resultado.error = f"salida inválida: {e}"
        if not resultado.ok and not resultado.error and proc.stderr:
            resultado.error = proc.stderr.strip().splitlines()[-1][:200]
        if resultado.ok:
            resultado.puntaje["total_s"] = t_total
            resultado.puntaje["ram_pico_mb"] = resultado.rss_mb
        resultados[engine] = resultado
        print(f"[{engine}] fin en {t_total} s\n", flush=True)

    print("=" * 78)
    print(f"{'Motor':<16}{'Carga s':>9}{'Infer s':>9}{'RAM MB':>9}  Puntaje")
    print("-" * 78)
    for engine in motores:
        r = resultados[engine]
        if not r.ok:
            print(f"{engine:<16}{'—':>9}{'—':>9}{'—':>9}  FALLO: {r.error[:60]}")
            continue
        partes = " ".join(f"{k}={v}" for k, v in r.puntaje.items())
        print(f"{engine:<16}{r.load_s:>9}{r.infer_s:>9}{r.rss_mb:>9}  {partes}")
    print("=" * 78)

    informe = {
        "imagen": imagen,
        "referencia": "salida oficial PP-Chart2Table (docs PaddleOCR, chart_parsing_02.png)",
        "resultados": {
            k: {
                "ok": v.ok, "error": v.error, "load_s": v.load_s,
                "infer_s": v.infer_s, "rss_mb": v.rss_mb, "puntaje": v.puntaje,
                "markdown": v.markdown[:2000], "textos": v.textos[:200],
            }
            for k, v in resultados.items()
        },
    }
    ruta = os.path.join(DIR_REPORTE, "reporte.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(informe, f, indent=2, ensure_ascii=False)
    print(f"\nInforme completo: {ruta}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "run":
        engine = args[args.index("--engine") + 1]
        imagen = args[args.index("--imagen") + 1]
        modo_hijo(engine, imagen)
    else:
        solo = None
        imagen = IMAGEN_DEFECTO
        if "--solo" in args:
            solo = args[args.index("--solo") + 1]
        if "--imagen" in args:
            imagen = args[args.index("--imagen") + 1]
        modo_padre(imagen, solo)
