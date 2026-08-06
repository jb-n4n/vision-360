#!/usr/bin/env python3
"""Daemon persistente de OCR de texto y vision IA (PP-OCRv6 + PP-DocBee).

Adaptado de better-ocr (https://github.com/jmbigi/better-ocr, CC BY-SA 4.0),
patron de la seccion 7.3 de su guia (mismo diseno que chart_server.py):
los modelos se cargan UNA sola vez en vez de por invocacion y el proceso se
cierra solo tras N segundos sin peticiones de inferencia.

Endpoints:
  POST /ocr  {"image": "<ruta>", "expected": ["texto", ...], "lang": "es"}
             -> {"ok": true, "texts": [...], "missing": [...], "all_found": bool}
  POST /ask  {"image": "<ruta>", "query": "pregunta en lenguaje natural"}
             -> {"ok": true, "answer": "..."}
  GET  /health -> {"status": "ok", "modelos": [...], "uptime_s": ...}

Idioma (regla del proyecto): es por defecto, nunca hardcodeado — el campo
opcional "lang" acepta es/pt/en.

Diseno:
  - HTTPServer de UN solo hilo a proposito: PaddleX NO es thread-safe, las
    peticiones de inferencia se procesan en serie por construccion.
  - El modelo de vision (PP-DocBee, ~7.7 GB) se carga perezosamente en la
    primera /ask, no al arrancar.
  - Un hilo vigia comprueba la inactividad (default 3600 s, --timeout).
    Nunca cierra mientras haya una inferencia en curso.
  - /health NO reinicia el temporizador: solo /ocr y /ask lo reinician.

Uso:
  python ocr_server.py --port 8081
  curl -X POST http://127.0.0.1:8081/ocr -H 'Content-Type: application/json' \
       -d '{"image": "test-results/ocr/strip-log.png", "expected": ["DH001"]}'
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, __import__("os").path.dirname(__file__))
from chart_server import vigia  # noqa: E402
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


def cargar_modelo_ocr(lang="es"):
    LOG.info("Cargando modelo PP-OCRv6 (lang=%s)...", lang)
    modelo = crear_modelo_ocr(lang=lang)  # device explicito dentro
    LOG.info("Modelo OCR cargado. Listo para recibir peticiones.")
    return modelo


def cargar_modelo_vision(engine="paddleocr-vl"):
    LOG.info("Cargando modelo de vision (%s, puede tardar)...", engine)
    modelo = crear_modelo_vision(engine)
    LOG.info("Modelo de vision cargado (%s).", engine)
    return modelo


def ollama_disponible():
    """True si el daemon de Ollama responde en 127.0.0.1:11434."""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def crear_handler(estado):
    """Construye la clase del handler con acceso al estado compartido.

    `estado` contiene: {"ocr", "vision", "lang", "inicio", "ultima_actividad",
    "ocupado"}. `estado["ocr"]` y `estado["vision"]` se rellenan la primera
    vez que se necesitan (la vision solo con una /ask).
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

        def _leer_cuerpo(self):
            largo = int(self.headers.get("Content-Length", 0))
            if largo <= 0:
                raise ValueError("Cuerpo JSON vacio")
            return json.loads(self.rfile.read(largo).decode("utf-8"))

        def do_GET(self):
            if self.path != "/health":
                self._enviar_json(404, {"ok": False, "error": "Solo GET /health, POST /ocr o POST /ask"})
                return
            self._enviar_json(200, {
                "status": "ok",
                "modelos": [
                    f"PP-OCRv6 (lang={estado.get('lang', 'es')})",
                    "PP-DocBee (cargado)" if estado.get("vision") is not None else "PP-DocBee (perezoso)",
                ],
                "ollama": ollama_disponible(),
                "uptime_s": round(time.time() - estado["inicio"]),
            })

        def do_POST(self):
            if self.path not in ("/ocr", "/ask"):
                self._enviar_json(404, {"ok": False, "error": "Solo GET /health, POST /ocr o POST /ask"})
                return
            try:
                datos = self._leer_cuerpo()
                imagen = datos["image"]
            except Exception as exc:
                self._enviar_json(400, {"ok": False, "error": f"JSON invalido: {exc}"})
                return
            if not os.path.isfile(imagen):
                self._enviar_json(400, {"ok": False, "error": f"Imagen no encontrada: {imagen}"})
                return

            estado["ocupado"] = True
            estado["ultima_actividad"] = time.time()
            try:
                if self.path == "/ocr":
                    self._procesar_ocr(datos, imagen)
                else:
                    self._procesar_ask(datos, imagen)
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

    return OcrHandler


def main():
    parser = argparse.ArgumentParser(description="Daemon de OCR de texto y vision con PaddleOCR")
    parser.add_argument("--host", default="127.0.0.1", help="Interfaz de escucha (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8081, help="Puerto (default: 8081)")
    parser.add_argument("--lang", default="es", help="Idioma OCR por defecto (default: es)")
    parser.add_argument("--ask-engine", default="docbee", choices=["paddleocr-vl", "docbee", "ollama", "gemma3"],
                        help="Motor de vision IA por defecto (default: docbee; ollama/gemma3 = Ollama local)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Segundos de inactividad antes de cerrarse (default: 3600 = 1 hora)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    estado = {
        "ocr": None,
        "vision": None,
        "vision_engine": None,
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