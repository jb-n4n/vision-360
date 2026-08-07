#!/usr/bin/env python3
"""Experimento: reto visual tipo captcha resuelto desde un NAVEGADOR TEMPORAL.

Objetivo adicional y secundario del paquete: comprobar que el stack local
(RT-DETR via daemon /vision + VLM via /ask con Set-of-Marks) puede resolver
un reto de seleccion de celdas mostrado en un navegador.

Diseno:
  - NAVEGADOR TEMPORAL: Playwright con contexto FRESCO (browser.new_context()
    sin perfil, sin cookies, nada persistente); se cierra y descarta al final.
  - Modo REAL (default, decision del programador): abre el demo oficial de
    reCAPTCHA v2, clickea el checkbox "I'm not a robot", espera el reto en su
    iframe (bframe), lee la instruccion desde el DOM (fallback OCR), captura
    la cuadricula, la resuelve y CLICKEA las celdas + VERIFY.
  - Modo --local: pagina sintetica 3x3 (file://, sin red) con validacion JS.
  - Pipeline de resolucion (daemon 127.0.0.1:8131):
      1. RT-DETR (POST /vision modo=objetos) -> celdas con objeto COCO.
      2. VLM + SoM (POST /ask engine=ollama) -> numeros de celda que menciona.
      3. Fusion simple: VLM decide; el detector apoya si el VLM no responde.

Uso:
  .venv-ocr/Scripts/python.exe scripts/experimento_captcha.py          # REAL por defecto
  .venv-ocr/Scripts/python.exe scripts/experimento_captcha.py --local  # demo sintetica
  .venv-ocr/Scripts/python.exe scripts/experimento_captcha.py --intentos 3  # reintentos tras rechazo

NOTA (decision del programador): el modo REAL es el comportamiento por
defecto por decision explicita del responsable del proyecto. --local genera
una pagina sintetica 3x3 para pruebas deterministas sin red. Si el reto es
rechazado, reCAPTCHA re-renderiza un reto nuevo y el experimento reintenta
automaticamente hasta --intentos rondas (default 3).

Requiere: daemon corriendo (.venv-ocr/Scripts/python.exe ocr_server.py
--port 8131 --timeout 0 --ask-engine ollama) y Ollama compartido en 11434.
"""

import argparse
import base64
import json
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DAEMON = "http://127.0.0.1:8131"
FOTO = Path(__file__).resolve().parent.parent / "ejemplos" / "test_charts" / "foto_personas.jpg"
UMBRAL_RTDETR = 0.6
TEMP = Path(tempfile.gettempdir()) / "opencode"

# Objetos clasicos de reCAPTCHA v2 que RT-DETR-L (COCO 80) puede detectar.
MAPEO_COCO = {
    "fire hydrant": "fire hydrant", "traffic light": "traffic light",
    "bus": "bus", "bicycle": "bicycle", "car": "car", "motorcycle": "motorcycle",
    "boat": "boat", "truck": "truck", "train": "train", "person": "person",
    "stop sign": "stop sign", "bench": "bench", "airplane": "airplane",
    "crosswalk": None, "stairs": None, "mountains": None,  # solo VLM
}


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


def _som(grid, out, n=3):
    """Numeros rojos sobre las celdas (Set-of-Marks). n = 3 (3x3) o 4 (4x4)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(grid).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=24)
    except TypeError:
        font = ImageFont.load_default()
    w, h = img.size
    cw, ch = w // n, h // n
    num = 1
    for r in range(n):
        for c in range(n):
            x0, y0 = c * cw, r * ch
            d.rectangle([x0, y0, x0 + cw, y0 + ch], outline=(255, 0, 0), width=3)
            d.text((x0 + 6, y0 + 4), str(num), fill=(255, 0, 0), font=font)
            num += 1
    img.save(out)
    return out


def _celdas_detector(grid, clase=None, n=3, umbral=UMBRAL_RTDETR):
    """RT-DETR via daemon: celdas con objeto por encima del umbral.
    Si `clase` no es None, solo cuenta detecciones de esa clase COCO."""
    from PIL import Image

    with Image.open(grid) as im:
        w, h = im.size
    res = _post("/vision", {"image": str(grid), "modo": "objetos"})
    celdas = {}
    for d in res.get("detecciones", []):
        if d["score"] < umbral:
            continue
        if clase and d["clase"] != clase:
            continue
        x1, y1, x2, y2 = d["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        num = (int(cy) // (h // n)) * n + (int(cx) // (w // n)) + 1
        celdas.setdefault(num, []).append((d["clase"], d["score"]))
    return celdas


RE_NUMEROS = re.compile(r"\b(?:[1-9]|1[0-6])\b")  # celdas 1..9 (3x3) o 1..16 (4x4)


def _celdas_vlm(grid, objeto, n=3):
    """VLM + SoM via daemon: numeros de celda que menciona el modelo."""
    som = _som(grid, TEMP / "captcha_som.png", n=n)
    res = _post("/ask", {
        "image": str(som),
        "query": (f"Hay {n * n} celdas numeradas del 1 al {n * n} con marcos rojos. "
                  f"Selecciona TODAS las celdas que contienen {objeto}. "
                  f"Responde SOLO con los numeros separados por coma."),
        "engine": "ollama",
    })
    texto = res.get("answer", "")
    numeros = [int(m) for m in RE_NUMEROS.findall(texto) if int(m) <= n * n]
    return texto, numeros


def _llamar_con_reintentos(llamar, num, intentos=2, espera_seg=3.0):
    """Llama llamar() (callable -> respuesta JSON o excepcion) hasta
    `intentos` veces, con `espera_seg` entre fallos. Devuelve la respuesta o
    None si todos fallan (celda saltada).

    Resistencia a reinicios del daemon (leccion 18, pendiente 3): el daemon
    puede reiniciarse a mitad del loop n*n de celdas; un reintento corto por
    celda deja que vuelva a estar operativo sin tumbar la resolucion."""
    for intento in range(1, intentos + 1):
        try:
            return llamar()
        except Exception as exc:
            if intento < intentos:
                print(f"  celda {num}: llamada {intento} fallo ({exc}); reintentando...")
                time.sleep(espera_seg)
            else:
                print(f"  celda {num}: error de deteccion ignorado tras {intentos} intentos ({exc})")
    return None


def _celdas_detector_por_celda(grid, clase=None, n=3, umbral=UMBRAL_RTDETR):
    """RT-DETR por celda recortada y AMPLIADA 2x.

    En la pasada de la cuadricula completa los objetos pequenos quedan bajo
    resolucion (leccion: buses/bicicletas ~0.5-0.9 en tiles de 126 px). El
    recorte por celda + LANCZOS 2x sube la resolucion del objeto y la
    deteccion. Mas lento (n*n llamadas) pero cabe en la ventana de
    expiracion del reto real (~2 min). Cada llamada a /vision se reintenta
    hasta 2 veces si el daemon se reinicia a mitad del loop (celda saltada
    solo si ambas fallan)."""
    from PIL import Image

    with Image.open(grid) as im:
        img = im.convert("RGB")
    w, h = img.size
    cw, ch = w // n, h // n
    celdas = {}
    for r in range(n):
        for c in range(n):
            num = r * n + c + 1
            crop = img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
            crop = crop.resize((cw * 2, ch * 2), Image.LANCZOS)
            tmp = TEMP / f"celda_{num}.png"
            crop.save(tmp)
            res = _llamar_con_reintentos(
                lambda: _post("/vision", {"image": str(tmp), "modo": "objetos"}),
                num)
            if res is None:
                continue
            for d in res.get("detecciones", []):
                if d["score"] < umbral:
                    continue
                if clase and d["clase"] != clase:
                    continue
                celdas.setdefault(num, []).append((d["clase"], d["score"]))
    return celdas


def resolver(grid, objeto_vlm, clase_rtdetr=None, n=3, usar_vlm=True,
             umbral=UMBRAL_RTDETR):
    """Resuelve la cuadricula y devuelve (celdas_elegidas, detalle).

    Fusion: si el objeto es COCO (RT-DETR disponible) mandan las celdas del
    detector por CELDA (recorte+2x: mas resolucion para objetos pequenos; el
    VLM sobre-selecciona y tarda ~2-3 min: el reto real expira en ~2 min);
    si no es COCO, mandan las del VLM."""
    if clase_rtdetr:
        detalle = {"detector": _celdas_detector_por_celda(grid, clase_rtdetr,
                                                          n=n, umbral=umbral)}
    else:
        detalle = {"detector": _celdas_detector(grid, None, n=n, umbral=umbral)}
    if usar_vlm:
        texto, numeros = _celdas_vlm(grid, objeto_vlm, n=n)
    else:
        texto, numeros = "(omitido: objeto COCO, detector suficiente)", []
    detalle["vlm"] = {"respuesta": texto, "numeros": numeros}
    if clase_rtdetr:
        elegidas = set(detalle["detector"])
    else:
        elegidas = set(numeros)
    if not elegidas:
        elegidas = set(detalle["detector"]) or set(numeros)
    return sorted(elegidas), detalle


# Plurales irregulares comunes de reCAPTCHA v2.
PLURALES = {
    "buses": "bus", "hydrants": "fire hydrant", "boxes": "box",
    "crosswalks": "crosswalk", "bicycles": "bicycle", "trucks": "truck",
    "boats": "boat", "cars": "car", "motorcycles": "motorcycle",
    "trains": "train", "airplanes": "airplane", "benches": "bench",
    "mountains": "mountain", "storefronts": "storefront",
    "street signs": "street sign", "stop signs": "stop sign",
}


def _instruccion_a_objeto(texto):
    """'Select all squares with buses' -> ('bus', clase COCO o None, permite_skip).

    permite_skip: True si la instruccion dice que se puede pasar sin marcar
    nada ("If there are none, click skip")."""
    permite_skip = "skip" in texto.lower()
    limpio = texto.strip().lower()
    for prefijo in ("select all squares with", "select all images with",
                    "select all tiles with", "select all pictures with",
                    "selecciona todas las imagenes con", "seleccione las imagenes con",
                    "marcar todas las imagenes con"):
        if limpio.startswith(prefijo):
            limpio = limpio[len(prefijo):].strip()
            break
    limpio = re.sub(r"^(a|an|the)\s+", "", limpio)  # "a fire hydrant" -> "fire hydrant"
    # Recorte del texto accesorio que sigue al objeto en el DOM real, que
    # viene CONCATENADO sin espacio ("traffic lightsIf there are none...").
    limpio = re.split(r"if there are none|if none|click verify|once there are none",
                      limpio, flags=re.IGNORECASE)[0]
    limpio = limpio.rstrip(".").strip()
    if not limpio:
        return None, None, permite_skip
    if limpio in MAPEO_COCO:
        return limpio, MAPEO_COCO[limpio], permite_skip
    if limpio in PLURALES:
        normalizado = PLURALES[limpio]
        return normalizado, MAPEO_COCO.get(normalizado), permite_skip
    singular = re.sub(r"(?<=[a-z])s$", "", limpio)  # "traffic lights" -> "traffic light"
    if singular in MAPEO_COCO:
        return singular, MAPEO_COCO[singular], permite_skip
    return limpio, None, permite_skip  # no COCO: solo VLM


def _ronda_reto(page):
    """Una ronda contra el reto real (dentro de un intento): detectar el reto
    en el iframe, leer la instruccion, capturar la cuadricula, resolverla,
    clickear celdas + VERIFY (o SKIP) y devolver el veredicto real del ancla
    (True si el checkbox quedo verificado)."""
    # 2. Reto en el iframe grande (bframe). El marcado real usa
    #    table.rc-imageselect-table-33/-44 y td.rc-imageselect-tile.
    frame = page.frame_locator("iframe[src*='bframe']")
    frame.locator("table.rc-imageselect-table, td.rc-imageselect-tile, img.rc-image-tile").first.wait_for(timeout=30000)
    print("Reto detectado en el iframe.")

    # Tamano de la cuadricula: 3x3 (9 tiles) o 4x4 (16 tiles). Tras un rechazo
    # Google APPENDEA una tabla nueva y deja la vieja en el DOM (leccion 18,
    # hallazgo 9): .last es la del reto ACTUAL; .first seria la resuelta.
    tablas = frame.locator("table.rc-imageselect-table-33, table.rc-imageselect-table-44, table.rc-imageselect-table")
    n_tablas = tablas.count()
    tabla = tablas.last
    if n_tablas > 1:
        print(f"  aviso: {n_tablas} tablas en el DOM; usando la ultima (reto actual).")
    try:
        n = 4 if "table-44" in (tabla.get_attribute("class") or "") else 3
    except Exception:
        n = 3
    print(f"Cuadricula {n}x{n}.")

    # 3. Instruccion desde el DOM (verdad de campo); fallback OCR.
    objeto = None
    permite_skip = False
    for sel in ("div.rc-imageselect-desc-no-canonical",
                "div.rc-imageselect-desc",
                "div.rc-imageselect-instructions"):
        try:
            txt = frame.locator(sel).first.text_content(timeout=4000)
            if txt and txt.strip():
                objeto, clase, permite_skip = _instruccion_a_objeto(txt)
                print(f"Instruccion (DOM): {txt.strip()!r} -> objeto={objeto!r} clase={clase!r} skip={permite_skip}")
                break
        except Exception:
            continue
    if objeto is None:
        page.screenshot(path=str(TEMP / "captcha_reto.png"), full_page=True)
        res = _post("/ocr", {"image": str(TEMP / "captcha_reto.png")})
        texto = " ".join(res.get("texts", []))
        objeto, clase, permite_skip = _instruccion_a_objeto(texto)
        print(f"Instruccion (OCR): {texto[:120]!r} -> objeto={objeto!r} clase={clase!r} skip={permite_skip}")

    # 4. Captura de la cuadricula (elemento tabla del iframe).
    grid = TEMP / "captcha_reto_grid.png"
    tabla.screenshot(path=str(grid))
    print(f"captura de la cuadricula -> {grid}")

    # 5. Resolucion. Objeto COCO -> solo RT-DETR (rapido, ~20 s: el reto real
    #    expira en ~2 min); objeto no-COCO -> VLM (lento, riesgo de expirar).
    #    Umbral rebajado para la clase objetivo: en grids reales los objetos
    #    pequenos puntuan ~0.5-0.6 (leccion: bicycles 0.55 bajo el 0.6).
    objeto_vlm = ("una persona" if not objeto else ("un/a " + objeto))
    usar_vlm = clase is None
    umbral = 0.45 if clase else UMBRAL_RTDETR
    elegidas, detalle = resolver(grid, objeto_vlm, clase_rtdetr=clase, n=n,
                                 usar_vlm=usar_vlm, umbral=umbral)
    print("\n== Resolucion del reto real ==")
    for num, dets in detalle["detector"].items():
        for c, score in dets:
            print(f"  RT-DETR: celda {num} ({c}, score {score:.2f})")
    print(f"  VLM SoM: {detalle['vlm']['respuesta']}")
    print(f"  celdas elegidas: {elegidas}")

    # 5b. "If there are none, click skip": sin candidatos y la instruccion lo
    #     permite -> SKIP en vez de marcar celdas (leccion: crosswalks real).
    skip_usado = False
    if not elegidas and permite_skip:
        for sel in ("button#recaptcha-skip-button", "div.rc-button-skip",
                    "button.rc-button-skip"):
            try:
                loc = frame.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.evaluate("el => el.click()")
                    print("SKIP clickeado (JS): sin celdas con el objeto.")
                    skip_usado = True
                    break
            except Exception:
                continue
        if not skip_usado:
            print("Instruccion permite SKIP pero no se encontro el boton.")

    # 6. Clic en las celdas (mapeo por bbox: robusto al orden del DOM). Los
    #    tiles se buscan DENTRO de la tabla actual (.last): si se usaran todos
    #    los del frame, contarian tambien los de la tabla vieja tras un
    #    rechazo (32 en vez de 16) y el mapeo quedaria desfasado (hallazgo 9).
    tiles = tabla.locator("td.rc-imageselect-tile")
    tb = tabla.bounding_box()
    n_tiles = tiles.count()
    print(f"  tiles en el DOM: {n_tiles}")
    for i in range(n_tiles):
        bb = tiles.nth(i).bounding_box()
        if not bb or not tb:
            continue
        col = round((bb["x"] - tb["x"]) / (tb["width"] / n))
        row = round((bb["y"] - tb["y"]) / (tb["height"] / n))
        num = row * n + col + 1
        if num in elegidas:
            # Click via JS: reCAPTCHA posiciona los tiles con transforms que
            # dejan el elemento "fuera del viewport" para el click normal de
            # Playwright; el click sintetico dispara el mismo handler del td.
            tiles.nth(i).evaluate("el => el.click()")
            print(f"  click (JS) en tile DOM[{i}] -> celda {num}")

    # 7. VERIFY (click JS: mismo motivo que los tiles, transforms fuera de
    #    viewport). Si ya se uso SKIP, no se clickea VERIFY.
    src_anterior = None
    if skip_usado:
        print("SKIP ya enviado: no se clickea VERIFY.")
    else:
        boton = None
        for sel in ("button#recaptcha-verify-button",
                    "div.rc-button-default", "button.rc-button-default"):
            try:
                loc = frame.locator(sel).first
                if loc.count() and loc.is_visible():
                    boton = loc
                    break
            except Exception:
                continue
        if boton is not None:
            # src del tile actual ANTES del VERIFY: es la grid que se manda a
            # revisar; _esperar_reto_nuevo la usa para detectar el re-render
            # (capturado aqui, el cambio SI se ve; capturado despues de los
            # 6 s de espera ya habria cambiado y el wait haria timeout,
            # hallazgo 9).
            try:
                src_anterior = frame.locator("img.rc-image-tile").last.evaluate(
                    "el => el.src")
            except Exception:
                src_anterior = None
            boton.evaluate("el => el.click()")
            print("VERIFY clickeado (JS).")
        else:
            print("No se encontro el boton VERIFY (marcado puede no haber cambiado).")

    # 8. Resultado real: el checkbox del ancla se marca como verificado si el
    #    reto se acepto; si no, Google re-renderiza un reto nuevo.
    page.wait_for_timeout(6000)
    ancla = page.frame_locator("iframe[src*='recaptcha']")
    verificada = ancla.locator("div.rc-anchor-checkbox-checked").count() > 0
    captura = TEMP / "captcha_real_resultado.png"
    page.screenshot(path=str(captura), full_page=True)
    print(f"captura final -> {captura}")
    return verificada, src_anterior


def _esperar_reto_nuevo(page, src_anterior=None, timeout=45000):
    """Espera el re-render del reto tras un rechazo: el src absoluto del
    ULTIMO tile de imagen cambia cuando Google re-renderiza la cuadricula
    (appendea una tabla nueva; la vieja queda en el DOM, hallazgo 9).

    src_anterior: src capturado ANTES de clickear VERIFY (la grid actual);
    si es None se captura aqui (caso: no hubo VERIFY). Tolerante: si no se
    detecta el cambio, se continua con lo que haya."""
    frame = next((f for f in page.frames if "bframe" in (f.url or "")), None)
    if frame is None:
        print("  aviso: no se encontro el iframe bframe; continuando.")
        return
    tile = frame.locator("img.rc-image-tile").last
    try:
        viejo = src_anterior or tile.evaluate("el => el.src")
    except Exception:
        viejo = None
    try:
        if viejo:
            frame.wait_for_function(
                "(src0) => { const t = document.querySelectorAll('img.rc-image-tile'); return t.length && t[t.length - 1].src !== src0; }",
                arg=viejo, timeout=timeout)
        else:
            frame.wait_for_function(
                "() => document.querySelector('img.rc-image-tile') !== null",
                timeout=timeout)
        print("  re-render del reto detectado (nueva imagen de tiles).")
    except Exception as exc:
        print(f"  aviso: no se detecto re-render del reto ({exc}); continuando.")


def _loop_reto(max_intentos, ronda, esperar):
    """Bucle de intentos: llama ronda() (callable -> bool: True = verificado)
    hasta max_intentos veces; entre rechazos llama esperar() para aguardar el
    re-render del reto. Devuelve True si alguna ronda verifico."""
    for intento in range(1, max_intentos + 1):
        print(f"\n== Intento {intento}/{max_intentos} ==")
        if ronda():
            return True
        if intento < max_intentos:
            print(f"Rechazado. Esperando re-render del reto (intento {intento + 1})...")
            esperar()
    return False


def _reto_real(page, max_intentos=3):
    """Loop completo contra el demo oficial: checkbox -> reto -> resolver ->
    clic; si el reto es rechazado, espera el re-render y reintenta hasta
    max_intentos rondas (leccion 18, pendiente 2)."""
    page.goto("https://www.google.com/recaptcha/api2/demo", timeout=60000)
    print("Demo REAL de reCAPTCHA v2 abierto en navegador temporal.")

    # 1. Click en el checkbox "I'm not a robot" (iframe ancla).
    checkbox = page.frame_locator("iframe[src*='recaptcha']").get_by_role(
        "checkbox", name="I'm not a robot")
    checkbox.click(timeout=20000)
    print("Checkbox clickeado. Esperando el reto...")

    # La ronda devuelve (verificada, src_anterior): el src del tile capturado
    # ANTES del VERIFY se pasa al espera de re-render (hallazgo 9).
    estado = {"src": None}

    def ronda():
        ok, src = _ronda_reto(page)
        estado["src"] = src
        return ok

    def esperar():
        _esperar_reto_nuevo(page, src_anterior=estado["src"])

    if _loop_reto(max_intentos, ronda=ronda, esperar=esperar):
        print("RESULTADO: checkbox VERIFICADO (reto superado).")
        return 0
    print(f"RESULTADO: reto rechazado tras {max_intentos} intentos.")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true",
                    help="Usar la demo sintetica local (file://) en vez del demo real de reCAPTCHA v2 (default)")
    ap.add_argument("--intentos", type=int, default=3,
                    help="Rondas maximas contra el reto real tras un rechazo (default 3; 1 = sin reintento)")
    args = ap.parse_args()
    if args.intentos < 1:
        print(f"Error: --intentos debe ser >= 1 (recibido {args.intentos})")
        return 2

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # Navegador TEMPORAL: contexto fresco y desechable, sin perfil ni cookies.
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        if not args.local:
            try:
                return _reto_real(page, max_intentos=args.intentos)
            finally:
                browser.close()  # temporal: nada persiste

        grid = TEMP / "captcha_grid.png"
        _html_demo()
        page.goto((TEMP / "captcha_demo.html").as_uri())
        page.wait_for_selector("#grid")
        page.locator("#grid").screenshot(path=str(grid))
        print(f"captura de la cuadricula -> {grid}")

        elegidas, detalle = resolver(grid, "una persona", clase_rtdetr="person",
                                     usar_vlm=False)
        print("\n== Resolucion (demo local) ==")
        for n, dets in detalle["detector"].items():
            for c, score in dets:
                print(f"  RT-DETR: celda {n} ({c}, score {score:.2f})")
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
