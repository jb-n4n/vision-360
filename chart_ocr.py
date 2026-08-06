#!/usr/bin/env python3
"""
Chart OCR — table extraction from charts with PP-Chart2Table (PaddleOCR).

Adapted from the better-ocr project (https://github.com/jmbigi/better-ocr,
CC BY-SA 4.0) following the operational lessons of its guide:
  - explicit device="cpu" (the default prefers GPU 0 when present)
  - robust access to PaddleX Result objects via .json (not json.dump)
  - one VLM instance per machine; PaddleX is NOT thread-safe

Usage:
    python chart_ocr.py <image> [--json] [--csv out.csv] [--raw out.json]

Exit code 0: the chart was parsed into a table (rows printed).
Exit code 1: parsing failed (no result / no 'result' key).
Exit code 2: image not found.

Requires the OCR venv (npm run test:ocr:setup). First run downloads
PP-Chart2Table (~2.2 GB); %TMP% needs > 3 GB free (OSError 122).
"""

import argparse
import json
import re
import sys
from io import StringIO

import pandas as pd


def es_fila_separadora(linea: str) -> bool:
    """True si la linea es una fila separadora de tabla markdown.

    Cubre los dos formatos posibles del modelo: '--- | ---' (sin pipe inicial)
    y '| --- | --- |' (con pipes). Cada celda debe ser solo guiones (3 o mas,
    como exige el estandar markdown), opcionalmente con ':' de alineacion
    (':---', '---:', ':---:'). Un guion simple o doble es un dato, no un
    separador."""
    celulas = [c.strip() for c in linea.strip().strip('|').split('|')]
    celulas = [c for c in celulas if c != '']
    return bool(celulas) and all(re.fullmatch(r':?-{3,}:?', c) for c in celulas)


def obtener_markdown(res):
    """Acceso robusto a la clave 'result' del objeto Result de PaddleX.

    La estructura puede variar: 'result' en raíz o dentro de 'res'.
    Devuelve el markdown de la tabla o lanza KeyError si no se encuentra.
    """
    if "result" in res.json:
        return res.json["result"]
    if "res" in res.json and "result" in res.json["res"]:
        return res.json["res"]["result"]
    print("Estructura JSON recibida:", json.dumps(res.json, indent=2))
    raise KeyError("No se encontró la clave 'result' en la respuesta. Revisa el JSON crudo.")


def markdown_a_df(markdown_tabla: str) -> pd.DataFrame:
    """Convierte el markdown del modelo a DataFrame limpio.

    1) Elimina filas separadoras ('---') y vacías.
    2) Convierte con separador pipe '|' (con espacios alrededor).
    3) Elimina columnas fantasmas (generadas por pipes al inicio/final).
    """
    lineas = markdown_tabla.splitlines()
    lineas_filtradas = [
        linea for linea in lineas
        if not es_fila_separadora(linea) and linea.strip() != ''
    ]
    markdown_limpio = "\n".join(lineas_filtradas).strip()

    df = pd.read_csv(StringIO(markdown_limpio), sep=r"\s*\|\s*", engine="python")
    df = df.dropna(axis=1, how='all')
    df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
    return df


def extraer_tabla(imagen: str):
    """Ejecuta PP-Chart2Table sobre la imagen y devuelve (df, raw_json)."""
    from paddleocr import ChartParsing  # import perezoso: no exigir paddleocr al importar

    model = ChartParsing(device="cpu")
    resultados = model.predict({"image": imagen})
    if not resultados:
        raise RuntimeError("No se obtuvo ningún resultado del modelo. Verifica la imagen.")

    res = resultados[0]
    raw = res.json if hasattr(res, "json") else dict(res)
    df = markdown_a_df(obtener_markdown(res))
    return df, raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Path to the chart image (PNG/JPEG)")
    parser.add_argument("--json", action="store_true", help="Print the raw model JSON to stdout")
    parser.add_argument("--csv", metavar="FILE", help="Write the extracted table to FILE")
    parser.add_argument("--raw", metavar="FILE", help="Write the raw model JSON to FILE")
    args = parser.parse_args()

    if not __import__("os").path.isfile(args.image):
        print(f"ERROR: image not found: {args.image}", file=sys.stderr)
        sys.exit(2)

    df, raw = extraer_tabla(args.image)

    if args.raw:
        with open(args.raw, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
        print(f"[chart-ocr] raw JSON -> {args.raw}")
    if args.csv:
        df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"[chart-ocr] table ({len(df)} rows x {len(df.columns)} cols) -> {args.csv}")
    if args.json:
        print(json.dumps(raw, ensure_ascii=False))

    print(f"[chart-ocr] extracted {len(df)} rows x {len(df.columns)} cols from {args.image}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
