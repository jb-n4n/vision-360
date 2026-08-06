#!/usr/bin/env python3
"""Servicio unico de OCR + Vision IA + Chart (daemon persistente, UN solo puerto).

Adaptado de better-ocr (https://github.com/jmbigi/better-ocr, CC BY-SA 4.0),
patron de la seccion 7.3 de su guia: los modelos se cargan UNA sola vez en vez
de por invocacion y el proceso se cierra solo tras N segundos sin peticiones
de inferencia (--timeout 0 = servicio permanente, para correr como tarea/servicio
compartido entre proyectos locales).

Endpoints:
  POST /ocr    {"image": "<ruta>", "expected": ["texto", ...], "lang": "es"}
               -> {"ok": true, "texts": [...], "missing": [...], "all_found": bool}
  POST /ask    {"image": "<ruta>", "query": "pregunta", "engine": "ollama|gemma3|docbee|paddleocr-vl"}
               -> {"ok": true, "answer": "..."}
  POST /chart  {"image": "<ruta o URL>"}
               -> {"ok": true, "filas": n, "markdown": "...", "csv": "..."}
  POST /vision {"image": "<ruta>", "modo": "auto|texto|graficos|doc|objetos|humano", "fallback": bool}
               -> resultado del modo (cascada rapida con gate; fallback VLM opcional)
  GET  /health -> {"status": "ok", "modelos": [...], "ollama": bool, "modos": [...], "uptime_s": ...}

Reglas del proyecto:
  - UN solo puerto para todos los proyectos locales (default 8131, el mismo que
    usa vision360.py --daemon-url). chart_server.py queda como CLI legacy.
  - Idioma es por defecto, nunca hardcodeado: campo opcional "lang" (es/pt/en).
  - Los motores Ollama (ollama/gemma3) delegan en el daemon local unico
    127.0.0.1:11434 (una sola instancia de Ollama compartida, ver
    scripts/servicio-ollama.ps1 y docs/INTEGRACION_PROYECTOS.md).

Diseno:
  - HTTPServer de UN solo hilo a proposito: PaddleX NO es thread-safe, las
    peticiones de inferencia se procesan en serie por construccion.
  - Modelos perezosos: OCR (PP-OCRv6) en la primera /ocr, vision (PP-DocBee/
    PaddleOCR-VL) en la primera /ask, chart (PP-Chart2Table) en la primera
    /chart, vision multi-modo en la primera /vision.
  - Un hilo vigia comprueba la inactividad (default 3600 s, --timeout; 0 = nunca
    cerrar). Nunca cierra mientras haya una inferencia en curso.
  - /health NO reinicia el temporizador: solo /ocr, /ask, /chart y /vision.

Uso:
  python ocr_server.py --port 8131
  curl -X POST http://127.0.0.1:8131/ocr -H 'Content-Type: application/json' \
       -d '{"image": "test-results/ocr/strip-log.png", "expected": ["DH001"]}'
"""

import argparse
import importlib.util
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, __import__("os").path.dirname(__file__))
from chart_server import df_a_markdown, vigia  # noqa: E402
from extractor_final import markdown_a_df, obtener_markdown  # noqa: E402
from ocr_verify import (  # noqa: E402
    comprobar_textos,
    crear_modelo_ocr,
    crear_modelo_vision,
    predecir_textos,
    preguntar,
)

LOG = logging.getLogger("ocr_server")

IDIOMAS_VALIDOS = {"es", "pt", "en"}
MOTORES_VALIDOS = {"paddleocr-vl", "docbee", "ollama", "gemma3"}

# El cuerpo solo contiene JSON con rutas/URLs: 1 MB sobra con margen.
# Limita la memoria de peticiones maliciosas o rotas (respuesta 413).
MAX_CUERPO = 1_048_576

MENSAJE_VENV = (
    "paddleocr no esta disponible en este interprete. Ejecuta el daemon con "
    "el venv del proyecto: .venv-ocr\\Scripts\\python.exe ocr_server.py (ver "
    "setup-ocr.ps1 para crearlo)."
)


def verificar_paddleocr():
    """Guard de prevencion: falla rapido con un mensaje claro si el interprete
    no tiene paddleocr (p. ej. daemon lanzado con el python del sistema en vez
    del venv). Se ejecuta ANTES de cualquier carga de modelo."""
    if importlib.util.find_spec("paddleocr") is None:
        raise RuntimeError(MENSAJE_VENV)


def cargar_modelo_ocr(lang="es"):
    verificar_paddleocr()
    LOG.info("Cargando modelo PP-OCRv6 (lang=%s)...", lang)
    modelo = crear_modelo_ocr(lang=lang)  # device explicito dentro
    LOG.info("Modelo OCR cargado. Listo para recibir peticiones.")
    return modelo


def cargar_modelo_vision(engine="paddleocr-vl"):
    verificar_paddleocr()
    LOG.info("Cargando modelo de vision (%s, puede tardar)...", engine)
    modelo = crear_modelo_vision(engine)
    LOG.info("Modelo de vision cargado (%s).", engine)
    return modelo


def cargar_modelo_chart():
    verificar_paddleocr()
    from paddleocr import ChartParsing  # import perezoso: VLM pesado

    LOG.info("Cargando modelo PP-Chart2Table (puede tardar ~95 s y 4.8 GB de RAM)...")
    modelo = ChartParsing(device="cpu")  # device explicito: el default prioriza GPU
    LOG.info("Modelo chart cargado. Listo para recibir peticiones.")
    return modelo


def puerto_ocupado(host, port):
    """True si hay un socket escuchando en host:port.

    Guard de prevencion: en Windows un segundo HTTPServer con SO_REUSEADDR
    puede 'secuestrar' las conexiones del primero en silencio; si el puerto ya
    esta en uso, el daemon debe fallar con un mensaje claro en lugar de correr
    duplicado."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.bind((host, port))
        except OSError:
            return True
        return False


def ollama_disponible():
    """True si el daemon unico de Ollama responde en 127.0.0.1:11434."""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def crear_handler(estado):
    """Construye la clase del handler con acceso al estado compartido.

    `estado` contiene: {"ocr", "vision", "chart", "lang", "inicio",
    "ultima_actividad", "ocupado"}. Los modelos se rellenan la primera vez
    que se necesitan (perezosos: OCR en /ocr, vision en /ask, chart en /chart).
    """

    class OcrHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            LOG.info("peticion %s: %s", self.address_string(), fmt % args)

        def _enviar_json(self, codigo, datos):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _recibir_json(self):
            """Lee y valida el cuerpo JSON. Devuelve (None, datos) o (codigo, error)."""
            try:
                largo = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return 400, "Content-Length invalida"
            if largo <= 0:
                return 400, "Cuerpo JSON vacio"
            if largo > MAX_CUERPO:
                try:
                    # Drenar el cuerpo (limitado) antes de cerrar: el cliente
                    # termina de enviar y recibe el 413 en lugar de un
                    # BrokenPipe/RST (race clasico de HTTP con cuerpos grandes).
                    self.rfile.read(min(largo, MAX_CUERPO))
                except Exception:
                    pass  # cliente ya cerro la conexion
                return 413, "Cuerpo demasiado grande"
            try:
                datos = json.loads(self.rfile.read(largo).decode("utf-8"))
            except Exception as exc:  # JSON invalido
                return 400, f"JSON invalido: {exc}"
            if not isinstance(datos, dict) or not isinstance(datos.get("image"), str):
                return 400, "Se espera un objeto JSON con la clave 'image' (ruta o URL de la imagen)"
            return None, datos

        def do_GET(self):
            if self.path != "/health":
                self._enviar_json(404, {"ok": False, "error": "Solo GET /health o POST /ocr, /ask, /chart, /vision"})
                return
            self._enviar_json(200, {
                "status": "ok",
                "modelos": [
                    f"PP-OCRv6 (lang={estado.get('lang', 'es')})",
                    "PP-DocBee (cargado)" if estado.get("vision") is not None else "PP-DocBee (perezoso)",
                    "PP-Chart2Table (cargado)" if estado.get("chart") is not None else "PP-Chart2Table (perezoso)",
                ],
                "modos": ["auto", "texto", "graficos", "doc", "objetos", "humano"],
                "ollama": ollama_disponible(),
                "uptime_s": round(time.time() - estado["inicio"]),
            })

        def do_POST(self):
            if self.path not in ("/ocr", "/ask", "/chart", "/vision"):
                self._enviar_json(404, {"ok": False, "error": "Solo GET /health o POST /ocr, /ask, /chart, /vision"})
                return

            error, datos = self._recibir_json()
            if error:
                self._enviar_json(error, {"ok": False, "error": datos})
                return
            if not os.path.isfile(datos["image"]):
                self._enviar_json(400, {"ok": False, "error": f"Imagen no encontrada: {datos['image']}"})
                return

            estado["ocupado"] = True
            estado["ultima_actividad"] = time.time()
            try:
                if self.path == "/ocr":
                    self._procesar_ocr(datos, datos["image"])
                elif self.path == "/ask":
                    self._procesar_ask(datos, datos["image"])
                elif self.path == "/chart":
                    self._procesar_chart(datos["image"])
                else:
                    self._procesar_vision(datos, datos["image"])
            except Exception as exc:
                LOG.exception("Error en la inferencia")
                self._enviar_json(500, {"ok": False, "error": str(exc)})
            finally:
                estado["ocupado"] = False

        def _procesar_ocr(self, datos, imagen):
            lang = datos.get("lang") or estado.get("lang") or "es"
            if lang not in IDIOMAS_VALIDOS:
                self._enviar_json(400, {"ok": False, "error": f"lang no soportado: {lang} (use es/pt/en)"})
                return
            estado["lang"] = lang
            if estado.get("ocr") is None:
                estado["ocr"] = cargar_modelo_ocr(lang=lang)
            LOG.info("Inferencia OCR iniciada para: %s", imagen)
            t0 = time.time()
            texts = predecir_textos(estado["ocr"], imagen)
            LOG.info("OCR completado en %.1f s (%d regiones)", time.time() - t0, len(texts))
            expected = [str(e) for e in datos.get("expected") or []]
            missing = comprobar_textos(texts, expected)
            self._enviar_json(200, {
                "ok": True,
                "texts": texts,
                "missing": missing,
                "all_found": not missing,
            })

        def _procesar_ask(self, datos, imagen):
            query = datos.get("query")
            if not query:
                self._enviar_json(400, {"ok": False, "error": "Falta el campo 'query'"})
                return
            engine = datos.get("engine") or estado.get("vision_engine") or "docbee"
            if engine not in MOTORES_VALIDOS:
                self._enviar_json(400, {"ok": False, "error": f"engine no soportado: {engine} (use paddleocr-vl/docbee/ollama/gemma3)"})
                return
            estado["vision_engine"] = engine
            if engine not in ("ollama", "gemma3") and (estado.get("vision") is None or estado.get("vision_engine") != engine):
                estado["vision"] = cargar_modelo_vision(engine)
                estado["vision_engine"] = engine
            LOG.info("Inferencia de vision (%s) iniciada para: %s", engine, imagen)
            t0 = time.time()
            answer = preguntar(estado.get("vision"), imagen, query, engine=engine)
            LOG.info("Vision (%s) completada en %.1f s", engine, time.time() - t0)
            self._enviar_json(200, {"ok": True, "answer": answer, "engine": engine})

        def _procesar_chart(self, imagen):
            if estado.get("chart") is None:
                estado["chart"] = cargar_modelo_chart()
            LOG.info("Inferencia chart iniciada para: %s", imagen)
            t0 = time.time()
            resultados = estado["chart"].predict({"image": imagen})
            if not resultados:
                raise RuntimeError("No se obtuvo ningun resultado del modelo.")
            df = markdown_a_df(obtener_markdown(resultados[0]))
            LOG.info("Chart completado en %.1f s (%d filas)", time.time() - t0, len(df))
            self._enviar_json(200, {
                "ok": True,
                "filas": len(df),
                "markdown": df_a_markdown(df),
                "csv": df.to_csv(index=False),
            })

        def _procesar_vision(self, datos, imagen):
            """POST /vision: enruta al modo indicado (import perezoso de
            vision.py: evita el ciclo ocr_server -> vision -> ocr_server)."""
            import vision

            modo = datos.get("modo", "auto")
            if modo not in vision.MODOS:
                self._enviar_json(400, {
                    "ok": False,
                    "error": f"modo invalido: {modo} (validos: {vision.MODOS})",
                })
                return
            con_fallback = bool(datos.get("fallback", False))
            LOG.info("Vision modo=%s para: %s", modo, imagen)
            resultado = vision.ejecutar(imagen, modo, con_fallback)
            self._enviar_json(200, resultado)

    return OcrHandler


def main():
    parser = argparse.ArgumentParser(description="Servicio unico de OCR + Vision IA + Chart")
    parser.add_argument("--host", default="127.0.0.1", help="Interfaz de escucha (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8131, help="Puerto unico (default: 8131)")
    parser.add_argument("--lang", default="es", help="Idioma OCR por defecto (default: es)")
    parser.add_argument("--ask-engine", default="docbee", choices=["paddleocr-vl", "docbee", "ollama", "gemma3"],
                        help="Motor de vision IA por defecto (default: docbee; ollama/gemma3 = Ollama local)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Segundos de inactividad antes de cerrarse (default: 3600 = 1 hora; 0 = servicio permanente)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Prevencion: la inferencia es de un solo hilo (PaddleX no es thread-safe);
    # un hilo de CPU evita problemas de OpenBLAS con multi-threads.
    os.environ["OMP_NUM_THREADS"] = "1"

    if puerto_ocupado(args.host, args.port):
        LOG.error("El puerto %s:%d ya esta en uso: hay otra instancia del daemon "
                  "corriendo (o la sesion anterior no cerro). Detenla antes de "
                  "arrancar otra.", args.host, args.port)
        sys.exit(2)

    estado = {
        "ocr": None,
        "vision": None,
        "vision_engine": None,
        "chart": None,
        "lang": args.lang,
        "inicio": time.time(),
        "ultima_actividad": time.time(),
        "ocupado": False,
    }
    server = HTTPServer((args.host, args.port), crear_handler(estado))

    def apagar(_sig, _frame):
        LOG.info("Senal recibida, cerrando...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, apagar)
    signal.signal(signal.SIGINT, apagar)

    LOG.info("ocr_server escuchando en http://%s:%d (lang default=%s, cierre automatico tras %d s de inactividad)",
             args.host, args.port, args.lang, args.timeout)

    if args.timeout > 0:
        threading.Thread(
            target=vigia,
            args=(server, estado, args.timeout),
            daemon=True,
        ).start()

    try:
        server.serve_forever()
    except Exception:
        LOG.exception("Error fatal")
        sys.exit(1)
    finally:
        server.server_close()
    LOG.info("Proceso finalizado. Modelos descargados de la memoria.")


if __name__ == "__main__":
    main()
