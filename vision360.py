#!/usr/bin/env python3
"""Vision IA 360 — reporte hibrido: Set-of-Marks + verdad de campo DOM + QA VLM por region.

Motivacion (investigacion): el conteo e inventario de UIs densas falla con VLM
puro (qwen2.5vl 5/6 paneles; inventario 2 de 5). La literatura recomienda
Set-of-Marks (recuadros numerados superpuestos) y procesamiento por tiles.
Este tool combina:

  1. --regions: JSON con las regiones reales del DOM
     [{"label": "...", "box": [x, y, w, h]}, ...] (coordenadas de la captura).
  2. som_overlay: superpone recuadros + numeros a la imagen y pregunta al
     VLM cuantos marcadores hay (conteo anclado visualmente).
  3. QA por region: recorta cada region (tiling) y pregunta al VLM.
  4. Reporte JSON: verdad de campo (DOM) + respuestas VLM + acierto.

Uso:
  python vision360.py --image shot.png --regions regions.json [--engine ollama]
                      [--ask-region 1 3] [--count-question "texto"] [--out reporte.json]

Requiere el daemon (ocr_server.py) corriendo en --daemon-url (default
http://127.0.0.1:8131) y el motor Ollama para respuestas rapidas.
"""

import argparse
import json
import sys
import urllib.request

SOM_COLOR = (255, 0, 0)
SOM_WIDTH = 3


def cargar_regiones(path):
    """Lee el JSON de regiones; lanza ValueError si no es una lista valida."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("regions debe ser una lista de {label, box}")
    out = []
    for entry in data:
        label = str(entry.get("label") or "")
        box = entry.get("box")
        if len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
            raise ValueError(f"box invalido en region '{label}': {box}")
        out.append({"label": label, "box": [float(v) for v in box]})
    return out


def som_overlay(image_path, regions, out_path):
    """Dibuja recuadros numerados (Set-of-Marks) sobre la imagen y la guarda.

    Devuelve la ruta de salida. Fuente por defecto usada para los numeros."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = None
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:
        font = ImageFont.load_default()
    for i, region in enumerate(regions, start=1):
        x, y, w, h = region["box"]
        draw.rectangle([x, y, x + w, y + h], outline=SOM_COLOR, width=SOM_WIDTH)
        draw.text((x + 4, y + 4), str(i), fill=SOM_COLOR, font=font)
    img.save(out_path)
    return out_path


def contar_dom(regions):
    """Conteo deterministico: el DOM es la verdad de campo."""
    return len(regions)


def preguntar_daemon(daemon_url, image, query, engine, timeout=3600):
    """Pregunta al daemon /ask; devuelve la respuesta o lanza RuntimeError."""
    payload = json.dumps({"image": image, "query": query, "engine": engine}).encode("utf-8")
    req = urllib.request.Request(
        daemon_url + "/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "daemon /ask fallo")
    return data.get("answer") or ""


def recortar_region(image_path, box, out_path):
    """Recorta una region de la imagen original (tiling)."""
    from PIL import Image

    x, y, w, h = [int(v) for v in box]
    Image.open(image_path).convert("RGB").crop((x, y, x + w, y + h)).save(out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Captura de pantalla (PNG/JPEG)")
    parser.add_argument("--regions", required=True, help="JSON con las regiones del DOM")
    parser.add_argument("--engine", default="ollama", help="Motor de vision IA (default: ollama)")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8131", help="URL del daemon OCR (default: 8131)")
    parser.add_argument("--ask-regions", nargs="*", type=int, default=[],
                        help="Regiones (1-based) para QA por recorte; vacio = ninguna")
    parser.add_argument("--count-question",
                        default="The image has several numbered red boxes drawn over it. "
                                "How many numbered boxes are there in total? Answer with just a number.",
                        help="Pregunta de conteo Set-of-Marks")
    parser.add_argument("--region-question",
                        default="Describe what this cropped region of a drilling visualization UI shows in one sentence.",
                        help="Pregunta para el QA por region (recorte)")
    parser.add_argument("--out", default=None, help="Ruta del reporte JSON (default: stdout)")
    args = parser.parse_args()

    regiones = cargar_regiones(args.regions)
    som_path = som_overlay(args.image, regiones, args.image + ".som.png")
    reporte = {
        "engine": args.engine,
        "imagen": args.image,
        "ground_truth_dom": {"region_count": contar_dom(regiones), "labels": [r["label"] for r in regiones]},
        "som": {"conteo_vlm": None, "pregunta": args.count_question},
        "regiones": [],
    }

    conteo = preguntar_daemon(args.daemon_url, som_path, args.count_question, args.engine)
    reporte["som"]["conteo_vlm"] = conteo
    import re
    numeros = re.findall(r"\d+", conteo)
    reporte["som"]["conteo_parseado"] = int(numeros[0]) if numeros else None

    for idx in args.ask_regions:
        if not (1 <= idx <= len(regiones)):
            reporte["regiones"].append({"index": idx, "error": "fuera de rango"})
            continue
        region = regiones[idx - 1]
        crop = recortar_region(args.image, region["box"], f"{args.image}.crop{idx}.png")
        pregunta = args.region_question
        respuesta = preguntar_daemon(args.daemon_url, crop, pregunta, args.engine)
        reporte["regiones"].append({
            "index": idx,
            "label": region["label"],
            "respuesta": respuesta,
        })

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(reporte, f, ensure_ascii=False, indent=2)
        print(f"[vision360] reporte -> {args.out}")
    else:
        print(json.dumps(reporte, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
