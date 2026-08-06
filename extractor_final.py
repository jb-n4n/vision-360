#!/usr/bin/env python3
"""Extracción de datos tabulares de gráficos con PP-Chart2Table (PaddleOCR).

Uso: python extractor_final.py [ruta/a/imagen.png]
     (por defecto usa ejemplos/grafico_demo.png)

Requiere: paddlepaddle==3.3.1 (CPU) y "paddleocr[doc-parser]" (ver requirements.txt).
Antes de la primera ejecución: export TMPDIR=/var/tmp (evitar OSError 122).
"""

import json
import os
import re
import sys
from io import StringIO

import pandas as pd


def es_archivo_imagen(ruta: str) -> bool:
    """True si el archivo tiene una firma mágica de imagen conocida.

    Formatos soportados por PaddleOCR: PNG, JPEG, BMP, GIF, WebP y TIFF.
    Se leen solo los 16 primeros bytes: barato y sin dependencias.
    Un directorio o un archivo de texto NO es una imagen aunque exista.
    """
    try:
        with open(ruta, "rb") as f:
            cabecera = f.read(16)
    except OSError:  # incluye IsADirectoryError
        return False
    return (
        cabecera.startswith(b"\x89PNG\r\n\x1a\n")       # PNG
        or cabecera.startswith(b"\xff\xd8\xff")          # JPEG
        or cabecera.startswith(b"GIF87a") or cabecera.startswith(b"GIF89a")
        or cabecera.startswith(b"BM")                    # BMP
        or (cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP")
        or cabecera.startswith(b"II*\x00")               # TIFF little-endian
        or cabecera.startswith(b"MM\x00*")               # TIFF big-endian
    )


def validar_imagen(imagen: str) -> str:
    """Valida que la ruta de la imagen exista y sea un archivo de imagen.

    Se ejecuta ANTES de cargar el modelo (que tarda 3-5 min y ocupa 4.8 GB
    de RAM): un typo en la ruta o un archivo que no es imagen no debe
    desperdiciar la carga del modelo.
    """
    if not os.path.exists(imagen):
        raise FileNotFoundError(
            f"La imagen no existe: '{imagen}'. Revisa la ruta antes de ejecutar."
        )
    if not es_archivo_imagen(imagen):
        raise ValueError(
            f"El archivo '{imagen}' existe pero no parece una imagen "
            "(se esperan PNG, JPEG, BMP, GIF, WebP o TIFF)."
        )
    return imagen


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
    Un 'result' presente pero vacio (None o '') se trata como ausente:
    la tabla extraida siempre tiene contenido si el modelo respondio bien.
    Devuelve el markdown de la tabla o lanza KeyError si no se encuentra.
    """
    if res.json.get("result"):
        return res.json["result"]
    res_anidado = res.json.get("res") or {}
    if res_anidado.get("result"):
        return res_anidado["result"]
    print("Estructura JSON recibida:", json.dumps(res.json, indent=2))
    raise KeyError("No se encontró la clave 'result' en la respuesta. Revisa salida_bruta.json.")


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

    if not markdown_limpio:
        raise ValueError(
            "El markdown del modelo está vacío (solo filas separadoras o líneas en blanco). "
            "Revisa salida_bruta.json: el modelo pudo no haber reconocido la tabla."
        )

    df = pd.read_csv(StringIO(markdown_limpio), sep=r"\s*\|\s*", engine="python")
    df = df.dropna(axis=1, how='all')
    df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
    return df


def main(imagen: str) -> None:
    validar_imagen(imagen)  # antes de cargar el modelo (3-5 min, 4.8 GB)
    from paddleocr import ChartParsing  # import perezoso: no exigir paddleocr al importar el modulo

    # 1. Inicializar el modelo (la primera ejecución descargará el modelo en TMPDIR)
    model = ChartParsing(device="cpu")  # device explícito: el default prioriza GPU si existe

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

    # 5-8. Acceso robusto a 'result' y conversión a DataFrame limpio
    df = markdown_a_df(obtener_markdown(res))

    # 9. Guardar resultado
    df.to_csv("datos_extraidos.csv", index=False, encoding='utf-8-sig')
    print("\n[OK] CSV guardado como 'datos_extraidos.csv'")
    print("Primeras filas del dato extraído:")
    print(df.head())


if __name__ == "__main__":
    ruta = sys.argv[1] if len(sys.argv) > 1 else "ejemplos/grafico_demo.png"
    main(ruta)
