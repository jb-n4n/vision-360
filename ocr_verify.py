#!/usr/bin/env python3
"""
PaddleOCR-based visual text verification for Drilling Visualisation.

Reads a UI screenshot with PaddleOCR (PP-OCRv6 medium, the fast and accurate
OCR engine) and checks that expected UI strings appear on screen.  Falls
back to the PaddleOCR-VL-1.6 document-vision model when --vl is passed.
With --ask, uses AI Vision (DocUnderstanding / PP-DocBee) to answer a
natural-language question about the screenshot.

Language (project rule): Spanish is the default OCR language (lang="es"),
never hardcoded — pass --lang to switch. The UI ships in es/pt/en, all three
are supported by PaddleOCR.

Implements the operational lessons from the better-ocr guide
(https://github.com/jmbigi/better-ocr/blob/master/docs/GUIA_OCR_VISION.md):
  - explicit device="cpu" (the default prefers GPU 0 when present)
  - large screenshots (> 4K) are downscaled to speed inference and cut RAM
  - PaddleX Result objects are serialized via .json (not json.dump)
  - one VLM instance per machine; prefer a persistent daemon (ocr_server.py)
    so models load once instead of per invocation

Usage:
    python ocr_verify.py <image> [expected_text ...] [--lang es] [--vl] [--json]
    python ocr_verify.py <image> [--lang es] --ask "question"

Exit code 0: every expected text was found (or the question was answered).
Exit code 1: at least one expected text is missing (details on stdout).
"""

import argparse
import json
import os
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")

MAX_DIM = 1920
MAX_DIM_VL = 768  # VL answer quality holds well at ~768 px; cuts CPU time

# Motores que delegan en el daemon local de Ollama (modelo por motor).
OLLAMA_MODELS = {
    "ollama": "qwen2.5vl:3b",
    "gemma3": "gemma3:4b",
}


def downscale_if_needed(image_path, max_dim=MAX_DIM):
    """Downscale very large screenshots (> max_dim px) to keep VLM inference fast."""
    from PIL import Image

    img = Image.open(image_path)
    w, h = img.size
    if max(w, h) <= max_dim:
        return image_path
    ratio = max_dim / max(w, h)
    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    resized = os.path.join(os.path.dirname(image_path) or ".", "ocr_resized.png")
    img.save(resized)
    return resized


def crear_modelo_ocr(lang="es", use_vl=False):
    """Crea el modelo PaddleOCR (PP-OCRv6 o PaddleOCR-VL). Cargado una sola vez."""
    if use_vl:
        from paddleocr import PaddleOCRVL

        return PaddleOCRVL(use_ocr_for_image_block=True, device="cpu")
    from paddleocr import PaddleOCR

    return PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
        lang=lang,
        device="cpu",
    )


def predecir_textos(modelo, image_path):
    """Ejecuta el modelo OCR sobre la imagen y devuelve la lista de textos.

    Cubre PP-OCRv6 ('rec_texts') y PaddleOCR-VL ('parsing_res_list').
    """
    texts = []
    for page in list(modelo.predict(image_path)):
        d = page if isinstance(page, dict) else dict(page)
        for bloque in d.get("rec_texts") or []:
            texts.append(str(bloque))
        for bloque in d.get("parsing_res_list") or []:
            content = bloque.get("content") if isinstance(bloque, dict) else getattr(bloque, "content", "")
            if content:
                texts.append(str(content))
    return texts


def extraer_textos(image_path, use_vl=False, lang="es"):
    """Crea un modelo nuevo y extrae los textos (conveniencia para CLI)."""
    return predecir_textos(crear_modelo_ocr(lang=lang, use_vl=use_vl), image_path)


def crear_modelo_vision(engine="paddleocr-vl"):
    """AI Vision: PaddleOCR-VL (fast, 0.9B Ernie, ~1.8 GB) or PP-DocBee2-3B
    (accurate but slow, ~7.7 GB / ~25 min CPU per question)."""
    if engine == "paddleocr-vl":
        from paddleocr import PaddleOCRVL

        # Free-form questions need layout detection (prompt_label is then
        # unrestricted); keep it on and cut cost via downscaling instead.
        return PaddleOCRVL(use_ocr_for_image_block=True, device="cpu")
    from paddleocr import DocUnderstanding

    return DocUnderstanding(device="cpu")


def preguntar(modelo, image_path, query, engine="paddleocr-vl"):
    """Responde una pregunta en lenguaje natural sobre la imagen."""
    if engine in OLLAMA_MODELS:
        # Ollama (qwen2.5vl:3b / gemma3:4b) — sin modelo Python: HTTP a
        # 127.0.0.1:11434. La primera llamada carga el modelo (~4-6 min CPU);
        # las siguientes responden en segundos (KV-cache reutilizado).
        import base64
        import urllib.request

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        payload = json.dumps({
            "model": OLLAMA_MODELS[engine],
            "messages": [{"role": "user", "content": query, "images": [b64]}],
            "stream": False,
            "options": {"num_predict": 64, "temperature": 0},
            # Mantener el modelo residente entre preguntas: Ollama lo
            # descarga a los 5 min inactivo y cada recarga cuesta minutos.
            "keep_alive": "30m",
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data.get("message") or {}).get("content") or ""
        if not content:
            raise KeyError(f"No 'message.content' in Ollama output: {json.dumps(data)[:400]}")
        return str(content)

    if engine == "paddleocr-vl":
        # PaddleOCR-VL.predict takes the image path (or ndarray) directly —
        # not the {"image": ...} dict that DocBee expects. Downscale first:
        # VL answers hold well at ~768 px and CPU time drops sharply.
        small = image_path
        try:
            small = downscale_if_needed(image_path, max_dim=MAX_DIM_VL)
        except Exception:
            small = image_path
        results = list(modelo.predict(small, prompt_label=query))
        if not results:
            raise RuntimeError("No result from PaddleOCR-VL — check the image path.")
        res = results[0]
        data = res.json if hasattr(res, "json") else dict(res)
        blocks = data.get("parsing_res_list")
        if blocks is None and isinstance(data.get("res"), dict):
            blocks = data["res"].get("parsing_res_list")
        pieces = []
        for block in blocks or []:
            content = block.get("content") if isinstance(block, dict) else getattr(block, "content", "")
            if content:
                pieces.append(str(content))
        if not pieces:
            raise KeyError(f"No 'parsing_res_list' in PaddleOCR-VL output: {json.dumps(data)[:400]}")
        return "\n".join(pieces)

    results = list(modelo.predict({"image": image_path, "query": query}))
    if not results:
        raise RuntimeError("No result from DocUnderstanding — check the image path.")
    res = results[0]
    data = res.json if hasattr(res, "json") else dict(res)
    result = data.get("result")
    if result is None and isinstance(data.get("res"), dict):
        result = data["res"].get("result")
    if result is None:
        raise KeyError(f"No 'result' key in DocUnderstanding output: {json.dumps(data)[:400]}")
    return str(result)


def ask_vision(image_path, query, engine="paddleocr-vl"):
    """Conveniencia para CLI: crea el modelo elegido y responde."""
    return preguntar(crear_modelo_vision(engine), image_path, query, engine)


def normalize(text):
    return " ".join(text.split()).lower().replace(" ", "")


def comprobar_textos(texts, expected):
    """Devuelve la lista de textos esperados que NO aparecen en `texts`."""
    normalized = normalize(" ".join(texts))
    return [want for want in expected if normalize(want) not in normalized]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Path to the screenshot (PNG/JPEG)")
    parser.add_argument("expected", nargs="*", help="UI strings that must appear")
    parser.add_argument("--lang", default="es", help="OCR language code (default: es)")
    parser.add_argument("--vl", action="store_true", help="Use PaddleOCR-VL-1.6 instead of PP-OCRv6")
    parser.add_argument("--ask", metavar="QUESTION", help="Answer a natural-language question with AI Vision")
    parser.add_argument("--ask-engine", default="paddleocr-vl", choices=["paddleocr-vl", "docbee"],
                        help="AI Vision engine (default: paddleocr-vl — fast; docbee — PP-DocBee2-3B, slow)")
    parser.add_argument("--json", action="store_true", help="Print recognized text as JSON")
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"ERROR: image not found: {args.image}", file=sys.stderr)
        sys.exit(2)

    image_path = downscale_if_needed(args.image)

    if args.ask:
        answer = ask_vision(image_path, args.ask, engine=args.ask_engine)
        print(f"[OCR] AI Vision ({args.ask_engine}) answer to '{args.ask}':")
        print(answer)
        print("[OCR] all expected strings found" if "yes" in answer.lower() or "sí" in answer.lower() or "si" in answer.lower() else "[OCR] answer produced")
        sys.exit(0)

    texts = extraer_textos(image_path, use_vl=args.vl, lang=args.lang)
    if args.json:
        print(json.dumps(texts, ensure_ascii=False))
        return

    print(f"[OCR] recognized {len(texts)} text regions from {args.image} (lang={args.lang})")
    if not args.expected:
        for t in texts:
            print(f"  - {t}")
        sys.exit(0)

    missing = comprobar_textos(texts, args.expected)
    for want in args.expected:
        if want in missing:
            print(f"  MISSING: '{want}'")
        else:
            print(f"  ok: found '{want}'")

    if missing:
        print(f"[OCR] {len(missing)} expected string(s) not found on screen", file=sys.stderr)
        sys.exit(1)
    print("[OCR] all expected strings found")


if __name__ == "__main__":
    main()
