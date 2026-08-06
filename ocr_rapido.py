#!/usr/bin/env python3
"""Ruta rápida de extracción de tablas de gráficos con PP-OCRv6.

Cascada: PP-OCRv6 lee los textos y sus cajas (bboxes) y empareja los
valores numéricos con las etiquetas de categoría por proximidad en X.
Un gate de plausibilidad decide si la tabla resultante es fiable; si no,
el llamador debe caer al modelo VLM ChartParsing (lento pero exacto).

Dos usos:
  - extraer_texto(imagen)      -> modo texto: líneas con texto, bbox y score
  - extraer_tabla(imagen)      -> modo gráficos: tabla markdown + DataFrame,
                                  o fallo con motivo si el gate no pasa

La función pura emparejar() es testeable sin paddleocr (modelo simulado).
"""

import os
import re
import tempfile
from dataclasses import dataclass, field

import pandas as pd

from extractor_final import es_fila_separadora, markdown_a_df

FRACCION_VENTANA = 0.5         # ventana de emparejado = 0.5 × espaciado entre años
RE_ANIO = re.compile(r"\d{4}")
RE_VALOR = re.compile(r"-?\d+(?:\.\d+)?")
MIN_ANIOS = 3                   # mínimo de categorías consecutivas para dar por buena la tabla


@dataclass
class ResultadoTabla:
    ok: bool = False
    motivo: str = ""
    markdown: str = ""
    df: pd.DataFrame = None
    textos: list = field(default_factory=list)


@dataclass
class LineaTexto:
    texto: str
    bbox: list  # 4 puntos [x, y]
    score: float


def limpiar_token(token: str) -> str:
    """Quita restos de OCR en valores ('90 -' -> '90', '50-' -> '50') SIN
    tocar el signo negativo ('-3.87' debe seguir siendo '-3.87')."""
    return token.strip().rstrip("- ").replace(",", ".")


def centro_x(bbox) -> float:
    return sum(p[0] for p in bbox) / len(bbox)


def es_anio(token: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", token))


def emparejar(textos: list[str], polis, ancho_imagen: float,
              scores: list[float] | None = None) -> ResultadoTabla:
    """Núcleo puro: empareja valores con categorías por proximidad en X.

    Entrada simulable en tests: lista de textos y lista de polígonos
    (arrays N×4×2), más el ancho de la imagen en píxeles. Si se pasan
    scores, un año candidato con score bajo se descarta cuando otro
    cubre casi la misma posición X con mejor score (ruido del OCR).
    """
    res = ResultadoTabla()
    if scores is None:
        scores = [1.0] * len(textos)
    anios: list[tuple[float, str, float]] = []
    valores: list[tuple[float, str]] = []

    for texto, poly, score in zip(textos, polis, scores):
        xc = centro_x(poly)
        limpio = limpiar_token(texto)
        if es_anio(limpio):
            anios.append((xc, limpio, score))
        elif RE_ANIO.search(texto):
            anios.append((xc, RE_ANIO.search(texto).group(), score))
        elif re.fullmatch(r"-?\d+(?:\.\d+)?", limpio):
            valores.append((xc, limpio))

    # Descartar candidatos duplicados: mismo año, misma zona X, peor score
    if len(anios) >= 2:
        anios.sort(key=lambda t: t[0])
        espaciado_est = (anios[-1][0] - anios[0][0]) / (len(anios) - 1)
        ventana_dup = 0.55 * espaciado_est
        unicos = [anios[0]]
        for cand in anios[1:]:
            previo = unicos[-1]
            if abs(cand[0] - previo[0]) < ventana_dup:
                if cand[2] > previo[2] + 1e-4:
                    unicos[-1] = cand
            else:
                unicos.append(cand)
        anios = unicos

    if len(anios) < MIN_ANIOS:
        res.motivo = (f"pocas categorías con formato de año: {len(anios)} "
                      f"(mínimo {MIN_ANIOS}); no se empareja")
        return res

    # Comprobar secuencia consecutiva
    secuencia = [a for _, a, _ in anios]
    consecutivos = True
    for a, b in zip(secuencia, secuencia[1:]):
        if int(b) - int(a) != 1:
            consecutivos = False
            break
    if not consecutivos:
        res.motivo = f"categorías no consecutivas: {secuencia}; riesgo de etiquetas sueltas"
        return res

    # Emparejar cada valor con el año más cercano, SOLO si está dentro de
    # una ventana estrecha (0.45 × espaciado): los ticks del eje Y quedan
    # a ~0.5 espaciado o más del primer año y se descartan; las etiquetas
    # de valor reales quedan a ≤0.3 espaciado de su barra.
    espaciado = (anios[-1][0] - anios[0][0]) / max(1, len(anios) - 1)
    ventana = FRACCION_VENTANA * espaciado
    centro_anio = {a: ax for ax, a, _ in anios}  # anio -> centro x
    emparejados: dict[str, list[tuple[float, str]]] = {a: [] for a in secuencia}
    for xc, v in valores:
        candidatos = [a for ax, a, _ in anios if abs(ax - xc) <= ventana]
        if not candidatos:
            continue
        anio = min(candidatos, key=lambda a: abs(centro_anio[a] - xc))
        emparejados[anio].append((xc, v))

    # Orden estable por posición X: las columnas quedan siempre de izq. a der.
    emparejados = {a: [v for _, v in sorted(vals)] for a, vals in emparejados.items()}

    if any(len(emparejados[a]) == 0 for a in secuencia):
        res.motivo = f"años sin valores emparejados: {[a for a in secuencia if not emparejados[a]]}"
        return res
    ncols = {len(emparejados[a]) for a in secuencia}
    if len(ncols) > 1:
        res.motivo = (f"número de valores inconsistente entre años "
                      f"({sorted(ncols)} columnas distintas); falla de lectura, "
                      f"se requiere el modelo VLM")
        return res

    # Construir tabla markdown (todas las filas con el mismo número de columnas)
    ncols = len(emparejados[secuencia[0]])
    lineas = ["| anio | " + " | ".join(f"v{i+1}" for i in range(ncols)) + " |"]
    lineas.append("| --- | " + " | ".join("---" for _ in range(ncols)) + " |")
    for a in secuencia:
        fila = [a] + emparejados[a]
        lineas.append("| " + " | ".join(fila) + " |")
    res.markdown = "\n".join(lineas)
    res.textos = textos
    try:
        res.df = markdown_a_df(res.markdown)
    except ValueError as e:
        res.motivo = f"markdown no convertible: {e}"
        return res
    res.ok = True
    return res


def interseccion(a, b) -> float:
    """IoU de dos cajas [x1,y1,x2,y2]."""
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter) if (area_a + area_b - inter) else 0.0


def caja_bbox(linea: LineaTexto) -> list:
    xs = [p[0] for p in linea.bbox]
    ys = [p[1] for p in linea.bbox]
    return [min(xs), min(ys), max(xs), max(ys)]


def combinar_lineas(principales: list[LineaTexto], banda: list[LineaTexto],
                    umbral=0.05, margen_y=50) -> list[LineaTexto]:
    """Fusión de OCR de imagen completa y de banda inferior.

    La banda inferior suele leer mejor las etiquetas del eje X (solapadas
    en la imagen completa por etiquetas de valores negativos). En conflicto
    por solapamiento (IoU > umbral) gana la lectura con mayor score; un
    duplicado por texto idéntico con centros próximos se descarta.
    """
    combinadas: list[LineaTexto] = list(banda) + list(principales)
    cajas = [caja_bbox(l) for l in combinadas]
    resultado = [combinadas[0]]
    cajas_ok = [cajas[0]]
    for linea, caja in zip(combinadas[1:], cajas[1:]):
        es_duplicado = False
        for previa, caja_ok in zip(resultado, cajas_ok):
            if interseccion(caja, caja_ok) > umbral:
                # gana la mejor lectura; epsilon para no reemplazar por
                # ruido de punto flotante (1.0 vs 0.999991)
                if linea.score > previa.score + 1e-4:
                    resultado[resultado.index(previa)] = linea
                    cajas_ok[cajas_ok.index(caja_ok)] = caja
                es_duplicado = True
                break
            if linea.texto == previa.texto:
                cx1, cy1 = (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2
                cx2, cy2 = (caja_ok[0] + caja_ok[2]) / 2, (caja_ok[1] + caja_ok[3]) / 2
                if abs(cy1 - cy2) < margen_y and abs(cx1 - cx2) < margen_y:
                    es_duplicado = True
                    break
        if es_duplicado:
            continue
        resultado.append(linea)
        cajas_ok.append(caja)
    return resultado


def extraer_texto(imagen: str, ocr_version: str = "PP-OCRv6") -> list[LineaTexto]:
    """Modo texto: líneas reconocidas con bbox y score (PP-OCRv6).

    Flags de orientacion/deswarpe desactivados (misma convencion que
    ocr_verify.py): evita rotaciones del pipeline que desalinean los bboxes
    con el marco de la imagen y recorta el tiempo de CPU."""
    from paddleocr import PaddleOCR  # import perezoso

    model = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
        device="cpu",
        ocr_version=ocr_version,
    )
    res = model.predict(imagen)
    res_json = res[0].json.get("res", {})
    textos = res_json.get("rec_texts", [])
    scores = res_json.get("rec_scores", [])
    polis = res_json.get("rec_polys", [])
    return [LineaTexto(t, list(poly), float(score))
            for t, poly, score in zip(textos, polis, scores)]


def extraer_tabla(imagen: str) -> ResultadoTabla:
    """Modo gráficos: extracción rápida con gate; fallo = usar ChartParsing."""
    from PIL import Image

    im = Image.open(imagen)
    ancho, alto = im.size
    lineas_principales = extraer_texto(imagen)

    # OCR adicional sobre la banda inferior (solo etiquetas del eje X, 6%
    # inferior): la imagen completa pierde años por solapamiento con
    # etiquetas de valores bajos, pero su lectura de VALORES es mejor, así
    # que la banda no debe cubrir las etiquetas de valor.
    fd, ruta_banda = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        im.crop((0, int(alto * 0.86), ancho, alto)).save(ruta_banda)
        lineas_banda = [
            LineaTexto(l.texto, [[x, y + int(alto * 0.86)] for x, y in l.bbox], l.score)
            for l in extraer_texto(ruta_banda)
        ]
    finally:
        try:
            os.remove(ruta_banda)
        except OSError:
            pass

    lineas = combinar_lineas(lineas_principales, lineas_banda)
    textos = [l.texto for l in lineas]
    polis = [l.bbox for l in lineas]
    res = emparejar(textos, polis, ancho, scores=[l.score for l in lineas])
    res.textos = textos
    if not res.ok:
        res.motivo = f"gate de plausibilidad: {res.motivo}"
    return res
