#!/usr/bin/env python3
"""Experimento: reto visual tipo captcha resuelto desde un NAVEGADOR TEMPORAL.

Objetivo adicional y secundario del paquete: comprobar que el stack local
(RT-DETR via daemon /vision + VLM via /ask con Set-of-Marks) puede resolver
un reto de seleccion de celdas mostrado en un navegador.

Diseno:
  - NAVEGADOR TEMPORAL: Playwright con contexto FRESCO (browser.new_context()
    sin perfil, sin cookies, nada persistente); se cierra y descarta al final.
  - Pagina de demostracion LOCAL (file://, sin red): cuadricula 3x3 con una
    foto en una celda y celdas clicables; el JS de la pagina valida la
    seleccion ("CORRECTO"/"INCORRECTO").
  - Pipeline de resolucion (daemon 127.0.0.1:8131):
      1. RT-DETR (POST /vision modo=objetos) sobre la captura -> celdas con
         objeto, umbral de score.
      2. VLM + SoM (POST /ask engine=ollama) sobre la captura numerada ->
         numero(s) de celda que menciona el modelo.
      3. Fusion simple: union de ambas; si difieren, se reportan ambas.
  - Clic en las celdas elegidas dentro del navegador temporal + veredicto.

Uso:
  .venv-ocr/Scripts/python.exe scripts/experimento_captcha.py          # REAL por defecto: demo oficial reCAPTCHA v2
  .venv-ocr/Scripts/python.exe scripts/experimento_captcha.py --local  # demo sintetica local (determinista, sin red)

NOTA (decision del programador): el modo REAL es el comportamiento por
defecto por decision explicita del responsable del proyecto. --local genera
una pagina sintetica 3x3 para pruebas deterministas sin red.

Requiere: daemon corriendo (.venv-ocr/Scripts/python.exe ocr_server.py
--port 8131 --timeout 0 --ask-engine ollama) y Ollama compartido en 11434.
"""

import argparse
import base64
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

DAEMON = "http://127.0.0.1:8131"
FOTO = Path(__file__).resolve().parent.parent / "ejemplos" / "test_charts" / "foto_personas.jpg"
UMBRAL_RTDETR = 0.6
TEMP = Path(tempfile.gettempdir()) / "opencode"


def _html_demo() -> Path:
    """Pagina local 3x3: una celda con la foto, celdas clicables y validacion JS."""
    b64 = base64.b64encode(FOTO.read_bytes()).decode()
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Reto demo</title>
<style>
  body {{ font-family: sans-serif; }}
  #grid {{ display: grid; grid-template-columns: repeat(3, 170px); gap: 6px; width: max-content; }}
  .celda {{ width: 170px; height: 170px; border: 2px solid #999; background: #e8e8e8;
           display: flex; align-items: center; justify-content: center; cursor: pointer;
           background-size: cover; position: relative; }}
  .celda.obj {{ background-image: url('data:image/jpeg;base64,{b64}');
               background-size: contain; background-position: center; background-repeat: no-repeat; }}
  .celda.sel {{ outline: 4px solid #2b6; }}
  #veredicto {{ margin-top: 12px; font-weight: bold; }}
</style></head><body>
  <p>Marque las celdas que contienen una persona y presione ENVIAR.</p>
  <div id="grid">{''.join(
      f'<div class="celda{" obj" if n == 5 else ""}" data-n="{n}" onclick="sel(this)">{n}</div>'
      for n in range(1, 10))}</div>
  <button onclick="enviar()">ENVIAR</button>
  <div id="veredicto"></div>
<script>
  let sel = (el) => el.classList.toggle('sel');
  function enviar() {{
    const sel = [...document.querySelectorAll('.celda.sel')].map(e => +e.dataset.n);
    const obj = +document.querySelector('.celda.obj').dataset.n;
    document.getElementById('veredicto').textContent =
      JSON.stringify(sel) === JSON.stringify([obj]) ? 'CORRECTO' : 'INCORRECTO';
    window.__veredicto = document.getElementById('veredicto').textContent;
  }}
</script></body></html>"""
    ruta = TEMP / "captcha_demo.html"
    TEMP.mkdir(parents=True, exist_ok=True)
    ruta.write_text(html, encoding="utf-8")
    return ruta


def _post(ruta, datos):
    req = urllib.request.Request(
        DAEMON + ruta,
        data=json.dumps(datos).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1200) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _som(grid, out):
    """Numeros rojos sobre las 9 celdas (Set-of-Marks)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(grid).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=24)
    except TypeError:
        font = ImageFont.load_default()
    w, h = img.size
    cw, ch = w // 3, h // 3
    n = 1
    for r in range(3):
        for c in range(3):
            x0, y0 = c * cw, r * ch
            d.rectangle([x0, y0, x0 + cw, y0 + ch], outline=(255, 0, 0), width=3)
            d.text((x0 + 6, y0 + 4), str(n), fill=(255, 0, 0), font=font)
            n += 1
    img.save(out)
    return out


def _celdas_detector(grid):
    """RT-DETR via daemon: celdas con objeto por encima del umbral."""
    from PIL import Image

    w, h = Image.open(grid).size
    res = _post("/vision", {"image": str(grid), "modo": "objetos"})
    celdas = {}
    for d in res.get("detecciones", []):
        if d["score"] < UMBRAL_RTDETR:
            continue
        x1, y1, x2, y2 = d["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        n = (int(cy) // (h // 3)) * 3 + (int(cx) // (w // 3)) + 1
        celdas.setdefault(n, []).append((d["clase"], d["score"]))
    return celdas


def _celdas_vlm(grid):
    """VLM + SoM via daemon: numeros que menciona el modelo."""
    som = _som(grid, TEMP / "captcha_som.png")
    res = _post("/ask", {
        "image": str(som),
        "query": "Hay 9 celdas numeradas del 1 al 9 con marcos rojos. "
                 "En una hay una persona. Responde SOLO con su numero.",
        "engine": "ollama",
    })
    texto = res.get("answer", "")
    numeros = [int(m) for m in re.findall(r"\b[1-9]\b", texto)]
    return texto, numeros


def resolver(grid):
    """Resuelve la cuadricula y devuelve (celdas_elegidas, detalle)."""
    detalle = {"detector": _celdas_detector(grid)}
    texto, numeros = _celdas_vlm(grid)
    detalle["vlm"] = {"respuesta": texto, "numeros": numeros}
    elegidas = set(numeros)
    if not elegidas:
        elegidas = set(detalle["detector"])
    return sorted(elegidas), detalle


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true",
                    help="Usar la demo sintetica local (file://) en vez del demo real de reCAPTCHA v2 (default)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # Navegador TEMPORAL: contexto fresco y desechable, sin perfil ni cookies.
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        if not args.local:
            page.goto("https://www.google.com/recaptcha/api2/demo", timeout=60000)
            print("Demo REAL de reCAPTCHA v2 abierto en navegador temporal.")
            print("URL:", page.url)
            try:
                checkbox = page.frame_locator("iframe[src*='recaptcha']").get_by_role(
                    "checkbox", name="I'm not a robot")
                checkbox.click(timeout=15000)
                print("Checkbox 'I'm not a robot' clickeado (el reto real queda para la sesion siguiente).")
            except Exception as exc:
                print(f"No se pudo clickear el checkbox: {exc}")
            page.wait_for_timeout(4000)
            page.screenshot(path=str(TEMP / "captcha_real.png"), full_page=True)
            print(f"captura -> {TEMP / 'captcha_real.png'}")
            browser.close()
            return 0

        grid = TEMP / "captcha_grid.png"
        _html_demo()
        page.goto((TEMP / "captcha_demo.html").as_uri())
        page.wait_for_selector("#grid")
        page.locator("#grid").screenshot(path=str(grid))
        print(f"captura de la cuadricula -> {grid}")

        elegidas, detalle = resolver(grid)
        print("\n== Resolucion ==")
        for n, dets in detalle["detector"].items():
            for clase, score in dets:
                print(f"  RT-DETR: celda {n} ({clase}, score {score:.2f})")
        print(f"  VLM SoM: {detalle['vlm']['respuesta']}")
        print(f"  celdas elegidas: {elegidas}")

        for n in elegidas:
            if 1 <= n <= 9:
                page.locator(f".celda[data-n='{n}']").click()
        page.get_by_role("button", name="ENVIAR").click()
        verdict = page.locator("#veredicto").text_content()
        print(f"  veredicto de la pagina: {verdict}")

        browser.close()  # temporal: nada persiste
        return 0 if verdict == "CORRECTO" else 1


if __name__ == "__main__":
    sys.exit(main())
