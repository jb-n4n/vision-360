#!/usr/bin/env python3
"""Valida la cascada de extracción sobre el set de gráficos de prueba.

Para cada PNG de ejemplos/test_charts/ con su CSV de referencia:
  1. Ruta rápida: PP-OCRv6 + emparejamiento geométrico (ocr_rapido).
  2. Si el gate falla: se marca "requiere fallback ChartParsing" (no se
     ejecuta el VLM salvo con --con-fallback, lento y 5.2 GB de RAM).
  3. Puntuación contra el CSV: etiquetas (columna 1) y valores numéricos.

Uso:
    /home/admin/venvs/paddle312/bin/python scripts/validar_cascada.py [--solo nombre.png]
    /home/admin/venvs/paddle312/bin/python scripts/validar_cascada.py --con-fallback
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from ocr_rapido import extraer_tabla  # noqa: E402

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "ejemplos", "test_charts")


def cargar_referencia(csv_path: str) -> tuple[list[str], list[list[str]]]:
    """Etiquetas y valores numéricos esperados (como strings normalizados)."""
    with open(csv_path, encoding="utf-8") as f:
        filas = list(csv.reader(f))[1:]  # sin cabecera
    etiquetas = [fila[0] for fila in filas if fila]
    valores = [[celda for celda in fila[1:] if celda != ""] for fila in filas if fila]
    return etiquetas, valores


def normalizar(v: str) -> float:
    """Float normalizado; tolera sufijos como '%' (35.0% == 35.0)."""
    return round(float(v.replace(",", ".").rstrip("%").strip()), 2)


def puntuar(etiquetas_esp, valores_esp, df) -> tuple[int, int]:
    """Aciertos de etiquetas y de valores numéricos (celda a celda)."""
    if df is None or df.empty:
        return 0, 0
    df = df.astype(str)
    aciertos_etiquetas = 0
    aciertos_valores = 0
    total_valores = sum(len(v) for v in valores_esp)
    for etiqueta, esperados in zip(etiquetas_esp, valores_esp):
        fila = df[df.iloc[:, 0].str.strip() == etiqueta]
        if fila.empty:
            continue
        aciertos_etiquetas += 1
        celdas = [str(c).strip() for c in fila.iloc[0, 1:].tolist() if str(c).strip() != ""]
        for esperado in esperados:
            if any(_misma_magnitud(c, esperado) for c in celdas):
                aciertos_valores += 1
    return aciertos_etiquetas, total_valores, aciertos_valores


def _misma_magnitud(c: str, esperado: str) -> bool:
    try:
        return abs(normalizar(c) - normalizar(esperado)) <= 0.01
    except ValueError:
        return False


def main() -> None:
    solo = None
    con_fallback = False
    if "--solo" in sys.argv:
        solo = sys.argv[sys.argv.index("--solo") + 1]
    if "--con-fallback" in sys.argv:
        con_fallback = True

    pngs = sorted(f for f in os.listdir(DIR) if f.endswith(".png"))
    if solo:
        pngs = [solo]

    if con_fallback:
        from paddleocr import ChartParsing  # import perezoso, 5.2 GB RAM
        print("Cargando ChartParsing (fallback)...")
        fallback = ChartParsing(device="cpu")
    else:
        fallback = None

    print(f"{'Imagen':<24}{'Tiempo s':>10}{'Etiquetas':>12}{'Valores':>12}  Estado")
    print("-" * 78)
    for png in pngs:
        ruta = os.path.join(DIR, png)
        csv_path = ruta.replace(".png", ".csv")
        if not os.path.exists(csv_path):
            print(f"{png:<24}{'—':>10}{'—':>12}{'—':>12}  sin CSV de referencia")
            continue
        etiquetas_esp, valores_esp = cargar_referencia(csv_path)

        t0 = time.monotonic()
        res = extraer_tabla(ruta)
        tiempo = round(time.monotonic() - t0, 1)

        if not res.ok and fallback is not None:
            t1 = time.monotonic()
            from extractor_final import obtener_markdown, markdown_a_df
            r = fallback.predict({"image": ruta})
            try:
                res.df = markdown_a_df(obtener_markdown(r[0]))
                res.ok = True
                res.motivo = "fallback ChartParsing"
            except (KeyError, ValueError) as e:
                res.motivo = f"fallback fallido: {e}"
            tiempo = round(time.monotonic() - t1 + tiempo, 1)

        if res.ok:
            n_et, total_val, n_val = puntuar(etiquetas_esp, valores_esp, res.df)
            estado = f"OK {n_et}/{len(etiquetas_esp)} {n_val}/{total_val}"
            if res.motivo:
                estado += f" [{res.motivo}]"
            print(f"{png:<24}{tiempo:>10}{f'{n_et}/{len(etiquetas_esp)}':>12}"
                  f"{f'{n_val}/{total_val}':>12}  {estado}")
        else:
            print(f"{png:<24}{tiempo:>10}{'—':>12}{'—':>12}  FALLO gate: {res.motivo}")


if __name__ == "__main__":
    main()
