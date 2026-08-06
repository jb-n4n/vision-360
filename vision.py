#!/usr/bin/env python3
"""CLI unificada de visión IA: texto, gráficos, documentos, objetos, auto.

Modos:
  auto     : clasifica la imagen (señales del OCR) y rutea al modo adecuado
  texto    : PP-OCRv6 -> líneas {texto, bbox, score} (JSON)
  graficos : cascada rápida PP-OCRv6 + emparejamiento geométrico; si el gate
             no pasa, fallback al VLM ChartParsing con --con-fallback
  doc      : PPStructureV3 solo layout (sin tabla/fórmula/chart, más ligero)
  objetos  : RT-DETR (PaddleX) -> detecciones con clase, score y caja
  humano   : alias de objetos filtrando la clase 'person'

Uso:
  python vision.py imagen.png [--modo auto|texto|graficos|doc|objetos|humano] [--salida json|csv|md] [--con-fallback]
  (en Windows con el venv: .venv-ocr/Scripts/python.exe vision.py ...)

La clasificación (clasificar) es pura y testeable sin modelos.
"""

import argparse
import json
import os
import sys

import pandas as pd

from extractor_final import markdown_a_df, obtener_markdown
from ocr_rapido import extraer_tabla, extraer_texto
from chart_server import df_a_markdown

MODOS = ["auto", "texto", "graficos", "doc", "objetos", "humano"]

# Consumo de RAM medido por motor (MB, ejecución real en esta máquina)
RAM_MOTORES = {
    "texto_v6": 1000,
    "graficos_rapido": 1000,
    "graficos_vlm": 5200,
    "doc": 4500,
    "objetos": 900,
}

# Perfiles por máquina: "completo" (general, sin límite) y "ligero" (equipos
# con poca RAM, p. ej. 4 GB: se bloquean los modos que podrían hacer OOM).
PERFILES = {
    "completo": {"ram_max_mb": None},
    "ligero": {"ram_max_mb": 3500},
}
ARCHIVO_PERFIL = "better_ocr.json"


def cargar_perfil() -> dict:
    """Perfil activo: env BETTER_OCR_PERFIL, override por better_ocr.json.

    La variable de entorno define el perfil base; el archivo opcional
    permite ajustes por máquina ({"perfil": "ligero", "ram_max_mb": 6000}).
    """
    perfil = os.environ.get("BETTER_OCR_PERFIL", "completo")
    ajustes = {}
    if os.path.exists(ARCHIVO_PERFIL):
        try:
            with open(ARCHIVO_PERFIL, encoding="utf-8") as f:
                ajustes = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            ajustes = {"error_archivo": str(exc)}
    perfil = ajustes.get("perfil", perfil)
    if perfil not in PERFILES:
        return {"nombre": perfil, "ram_max_mb": None,
                "error": f"perfil desconocido: {perfil} (validos: {list(PERFILES)})"}
    perfil_data = dict(PERFILES[perfil])
    perfil_data["nombre"] = perfil
    if isinstance(ajustes.get("ram_max_mb"), (int, float)) and not isinstance(ajustes.get("ram_max_mb"), bool):
        perfil_data["ram_max_mb"] = int(ajustes["ram_max_mb"])
    if "error_archivo" in ajustes:
        perfil_data["error_archivo"] = ajustes["error_archivo"]
    return perfil_data


def chequear_ram(modo: str, perfil: dict) -> dict | None:
    """Devuelve un error si el motor del modo supera el límite del perfil."""
    if perfil.get("error"):
        return {"ok": False, "error": perfil["error"]}
    limite = perfil.get("ram_max_mb")
    if not limite:
        return None
    requerido = RAM_MOTORES.get(modo)
    if requerido and requerido > limite:
        return {
            "ok": False,
            "error": (f"modo '{modo}' requiere ~{requerido} MB de RAM, supera el "
                      f"limite del perfil '{perfil['nombre']}' ({limite} MB). "
                      f"Usa BETTER_OCR_PERFIL=completo o better_ocr.json"),
        }
    return None


def clasificar(textos: list, polis, ancho: float, alto: float) -> tuple[str, str]:
    """Clasifica la imagen por señales del OCR. Devuelve (modo, motivo).

    Orden de las reglas:
      1. Categorías tipo año consecutivas + valores consistentes -> graficos
      2. Página densa de texto (muchas líneas, cobertura amplia) -> doc
      3. Poco o ningún texto -> objetos (foto, dibujo, pintura)
      4. Texto disperso -> texto
    """
    from ocr_rapido import emparejar

    if polis:
        resultado = emparejar(textos, polis, ancho)
        if resultado.ok:
            return "graficos", "categorías consecutivas y valores consistentes"

    n = len(textos)
    area_total = max(1.0, ancho * alto)
    area_texto = 0.0
    for poly in polis:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        area_texto += max(0.0, max(xs) - min(xs)) * max(0.0, max(ys) - min(ys))
    cobertura = area_texto / area_total

    if n >= 50 and cobertura >= 0.08:
        return "doc", f"{n} líneas de texto con cobertura {cobertura:.0%}: página de documento"
    if n <= 3:
        return "objetos", f"solo {n} líneas de texto: imagen sin contenido textual (foto/dibujo)"
    return "texto", f"{n} líneas de texto disperso"


def df_a_json(df: pd.DataFrame) -> dict:
    return {"filas": len(df), "columnas": list(df.columns),
            "tabla": df.astype(str).values.tolist()}


def modo_texto(imagen: str) -> dict:
    lineas = extraer_texto(imagen)
    return {"ok": True, "lineas": [
        {"texto": l.texto, "score": round(l.score, 4),
         "bbox": [[round(p[0]), round(p[1])] for p in l.bbox]}
        for l in lineas
    ]}


def modo_graficos(imagen: str, con_fallback: bool) -> dict:
    res = extraer_tabla(imagen)
    if res.ok:
        return {"ok": True, "motor": "PP-OCRv6+emparejamiento", **df_a_json(res.df)}
    if not con_fallback:
        return {"ok": False, "motor": "PP-OCRv6",
                "error": f"gate de plausibilidad: {res.motivo}",
                "sugerencia": "reintentar con --con-fallback (VLM ChartParsing, 5.2 GB)"}
    from paddleocr import ChartParsing  # import perezoso: VLM pesado
    modelo = ChartParsing(device="cpu")
    resultados = modelo.predict({"image": imagen})
    if not resultados:
        return {"ok": False, "error": "El modelo VLM no devolvió resultados"}
    df = markdown_a_df(obtener_markdown(resultados[0]))
    return {"ok": True, "motor": "ChartParsing (VLM, fallback)", **df_a_json(df)}


def modo_doc(imagen: str) -> dict:
    from paddleocr import PPStructureV3  # import perezoso
    modelo = PPStructureV3(
        device="cpu",
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_table_recognition=False,
        use_formula_recognition=False,
        use_seal_recognition=False,
        use_region_detection=False,
        use_chart_recognition=False,
    )
    res = modelo.predict(imagen)
    if not res:
        return {"ok": False, "error": "El pipeline no devolvió resultados"}
    md = getattr(res[0], "markdown", None)
    markdown_texto = (md or {}).get("markdown_texts", "") if isinstance(md, dict) else ""
    json_res = res[0].json.get("res", {})
    bloques = json_res.get("layout_det_res", {}).get("boxes", []) or []
    return {"ok": True, "bloques": len(bloques),
            "bloques_detalle": [
                {"etiqueta": b.get("label", ""),
                 "score": round(float(b.get("score", 0)), 3),
                 "bbox": [round(v, 1) for v in b.get("coordinate", [])]}
                for b in bloques[:20]
            ],
            "markdown": markdown_texto[:10000]}


def modo_objetos(imagen: str, solo_personas: bool) -> dict:
    # mkldnn roto en paddlepaddle 3.3.1 (PIR + oneDNN, issue #18162): se
    # desactiva por defecto ANTES de importar paddlex (lee el flag al importar)
    os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
    from paddlex import create_model  # import perezoso
    modelo = create_model("RT-DETR-L")
    res = list(modelo.predict(imagen))  # predict es generador
    if not res:
        return {"ok": False, "error": "La detección no devolvió resultados"}
    detecciones = []
    for det in (res[0].json.get("res", {}).get("boxes", []) or []):
        clase = det.get("label", det.get("class_name", ""))
        if solo_personas and clase != "person":
            continue
        detecciones.append({
            "clase": clase,
            "score": round(float(det.get("score", 0)), 4),
            "bbox": [round(v, 1) for v in det.get("coordinate", det.get("bbox", []))],
        })
    return {"ok": True, "detecciones": detecciones}


def ejecutar(imagen: str, modo: str, con_fallback: bool) -> dict:
    perfil = cargar_perfil()
    clave_ram = {"texto": "texto_v6", "graficos": "graficos_rapido",
                 "doc": "doc", "objetos": "objetos", "humano": "objetos"}.get(modo)
    if con_fallback and modo == "graficos":
        clave_ram = "graficos_vlm"
    if clave_ram:
        error = chequear_ram(clave_ram, perfil)
        if error:
            error["perfil"] = {"nombre": perfil.get("nombre"),
                               "ram_max_mb": perfil.get("ram_max_mb")}
            return error
    if modo == "auto":
        lineas = extraer_texto(imagen)
        ancho, alto = _dimensiones(imagen)
        modo, motivo = clasificar(
            [l.texto for l in lineas], [l.bbox for l in lineas], ancho, alto)
        resultado = ejecutar(imagen, modo, con_fallback)
        resultado["clasificacion"] = {"modo": modo, "motivo": motivo}
        return resultado
    if modo == "texto":
        resultado = modo_texto(imagen)
    elif modo == "graficos":
        resultado = modo_graficos(imagen, con_fallback)
    elif modo == "doc":
        resultado = modo_doc(imagen)
    elif modo == "objetos":
        resultado = modo_objetos(imagen, solo_personas=False)
    elif modo == "humano":
        resultado = modo_objetos(imagen, solo_personas=True)
    else:
        return {"ok": False, "error": f"modo desconocido: {modo}"}
    resultado["perfil"] = {"nombre": perfil.get("nombre"),
                           "ram_max_mb": perfil.get("ram_max_mb")}
    return resultado


def _dimensiones(imagen: str) -> tuple[int, int]:
    try:
        from PIL import Image
        return Image.open(imagen).size
    except OSError:
        return 0, 0


def _salida(datos: dict, formato: str) -> str:
    if formato == "json":
        return json.dumps(datos, ensure_ascii=False, indent=2)
    if formato == "csv" and datos.get("tabla"):
        df = pd.DataFrame(datos["tabla"], columns=datos["columnas"])
        return df.to_csv(index=False)
    if formato == "md" and datos.get("tabla"):
        df = pd.DataFrame(datos["tabla"], columns=datos["columnas"])
        return df_a_markdown(df)
    return json.dumps(datos, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI unificada de visión IA")
    parser.add_argument("imagen", help="Ruta a la imagen")
    parser.add_argument("--modo", choices=MODOS, default="auto",
                        help="Modo de visión (default: auto)")
    parser.add_argument("--salida", choices=["json", "csv", "md"], default="json")
    parser.add_argument("--con-fallback", action="store_true",
                        help="En modo graficos, usar el VLM ChartParsing si el gate falla")
    args = parser.parse_args()

    if not os.path.exists(args.imagen):
        print(json.dumps({"ok": False, "error": f"La imagen no existe: {args.imagen}"}))
        sys.exit(1)

    resultado = ejecutar(args.imagen, args.modo, args.con_fallback)
    print(_salida(resultado, args.salida))
    if not resultado.get("ok", True):
        sys.exit(2)


if __name__ == "__main__":
    main()
