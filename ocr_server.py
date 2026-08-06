#!/usr/bin/env python3
"""Servicio unico de OCR + Vision IA + Chart (daemon persistente, UN solo puerto).

Adaptado de better-ocr (https://github.com/jmbigi/better-ocr, CC BY-SA 4.0),
patron de la seccion 7.3 de su guia: los modelos se cargan UNA sola vez en vez
de por invocacion y el proceso se cierra solo tras N segundos sin peticiones
de inferencia (--timeout 0 = servicio permanente, para correr como tarea/servicio
compartido entre proyectos locales).

Endpoints:
  POST /ocr    {"image": "<ruta>", "expected": ["texto", ...], "lang": "es", "async": bool, "espera_s": int}
               -> {"ok": true, "texts": [...], "missing": [...], "all_found": bool}
  POST /ask    {"image": "<ruta>", "query": "pregunta", "engine": "ollama|gemma3|docbee|paddleocr-vl"}
               -> {"ok": true, "answer": "..."}
  POST /chart  {"image": "<ruta o URL>"}
               -> {"ok": true, "filas": n, "markdown": "...", "csv": "..."}
  POST /vision {"image": "<ruta>", "modo": "auto|texto|graficos|doc|objetos|humano", "fallback": bool}
               -> resultado del modo (cascada rapida con gate; fallback VLM opcional)
  GET  /health -> {"status": "ok", "modelos": [...], "ollama": bool, "modos": [...], "cola": {...}, "uptime_s": ...}
  GET  /resultado/<job_id> -> estado y resultado de un trabajo (polling async)

Canalizacion y cola (diseno):
  - Todas las peticiones de inferencia entran en UNA cola FIFO con UN solo
    worker: PaddleX NO es thread-safe, la inferencia se serializa por
    construccion y el servidor HTTP NUNCA se bloquea durante una inferencia
    (/health y /resultado responden al instante, incluso con un chart de 5 min
    en curso).
  - Modo sincrono (default): la peticion espera el resultado (espera_s, default
    600 s); si se agota, responde 503 con job_id para recuperarlo por polling.
  - Modo async: "async": true -> 202 {job_id}; se recupera con GET /resultado/<id>.
  - Resultados retenidos en memoria (ultimos 100); 404 si el job ya expiro.

Reglas del proyecto:
  - UN solo puerto para todos los proyectos locales (default 8131, el mismo que
    usa vision360.py --daemon-url). chart_server.py queda como CLI legacy.
  - Idioma es por defecto, nunca hardcodeado: campo opcional "lang" (es/pt/en).
  - Los motores Ollama (ollama/gemma3) delegan en el daemon local unico
    127.0.0.1:11434 (una sola instancia de Ollama compartida, ver
    scripts/servicio-ollama.ps1 y docs/INTEGRACION_PROYECTOS.md).

Uso:
  python ocr_server.py --port 8131
  curl -X POST http://127.0.0.1:8131/ocr -H 'Content-Type: application/json' \
       -d '{"image": "test-results/ocr/strip-log.png", "expected": ["DH001"]}'
  # async + polling:
  curl -X POST http://127.0.0.1:8131/chart -H 'Content-Type: application/json' \
       -d '{"image": "ejemplos/grafico_demo.png", "async": true}'
  curl http://127.0.0.1:8131/resultado/<job_id>
"""

import argparse
import importlib.util
import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
import time
import uuid
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
MODOS_VISION = ["auto", "texto", "graficos", "doc", "objetos", "humano"]

# El cuerpo solo contiene JSON con rutas/URLs: 1 MB sobra con margen.
# Limita la memoria de peticiones maliciosas o rotas (respuesta 413).
MAX_CUERPO = 1_048_576
# Cola FIFO acotada: si esta llena, las peticiones nuevas fallan con 503
# inmediato en lugar de encolar sin limite (proteccion contra tormentas).
MAX_COLA = 50
# Resultados retenidos para polling async (los mas antiguos se descartan).
MAX_RESULTADOS = 100
# Espera del modo sincrono por defecto (s). Una inferencia real puede tardar
# minutos (chart ~5 min CPU): el cliente siempre puede pasar "espera_s".
ESPERA_DEFAULT_S = 600

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


# Caché de disponibilidad de Ollama: /health debe responder al instante SIEMPRE
# (la comprobacion de red puede tardar hasta 2 s si el daemon no responde).
OLLAMA_CACHE_S = 15
_ollama_cache = {"ts": 0.0, "ok": False}
_lock_ollama = threading.Lock()


def ollama_disponible():
    """True si el daemon unico de Ollama responde en 127.0.0.1:11434.

    Resultado cacheado OLLAMA_CACHE_S segundos: /health no debe bloquearse
    con un timeout de red en cada peticion."""
    ahora = time.time()
    with _lock_ollama:
        if ahora - _ollama_cache["ts"] < OLLAMA_CACHE_S:
            return _ollama_cache["ok"]
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:11434/api/version",
                                        timeout=2) as resp:
                ok = resp.status == 200
        except Exception:
            ok = False
        _ollama_cache.update({"ts": ahora, "ok": ok})
        return ok


# ---------------------------------------------------------------------------
# Cola de trabajos: canaliza y serializa la inferencia (PaddleX no es thread-safe)
# ---------------------------------------------------------------------------

class Trabajo:
    """Un trabajo de inferencia. El worker ejecuta 'fn(estado)' y guarda el
    resultado o el error en 'resultado'/'error', siempre con 'evento'."""

    __slots__ = ("job_id", "tipo", "fn", "creado", "iniciado",
                 "resultado", "error", "evento", "seq")

    def __init__(self, tipo, fn):
        self.job_id = uuid.uuid4().hex[:12]
        self.tipo = tipo
        self.fn = fn
        self.creado = time.time()
        self.iniciado = None
        self.resultado = None
        self.error = None
        self.evento = threading.Event()


class ServicioCola:
    """Cola FIFO acotada con un solo worker (serializacion por construccion).

    - enviar(): encola y devuelve el Trabajo (nunca bloquea).
    - El worker ejecuta los trabajos en orden; los modelos perezosos se cargan
      dentro del worker (el servidor HTTP nunca se bloquea con inferencia).
    - resumen(): informacion para /health y /resultado de TODOS los trabajos
      (en_cola, en_curso o finalizados), con posicion aproximada en la cola.
    """

    def __init__(self, estado, max_cola=MAX_COLA, max_resultados=MAX_RESULTADOS):
        self.estado = estado
        self.cola = queue.Queue(maxsize=max_cola)
        self.max_resultados = max_resultados
        self._jobs = {}          # job_id -> Trabajo (pendientes y finalizados)
        self._seq_cola = []      # secuencias de los trabajos esperando en la cola
        self._seq = 0
        self._lock = threading.Lock()
        self._en_curso = None    # Trabajo en ejecucion o None
        self._worker = threading.Thread(target=self._bucle, daemon=True)
        self._worker.start()

    # -- API publica --------------------------------------------------------

    def enviar(self, tipo, fn):
        """Encola un trabajo. Lanza queue.Full si la cola esta llena."""
        trabajo = Trabajo(tipo, fn)
        with self._lock:
            self._seq += 1
            trabajo.seq = self._seq
            self._jobs[trabajo.job_id] = trabajo
        try:
            self.cola.put_nowait(trabajo)
        except queue.Full:
            with self._lock:
                del self._jobs[trabajo.job_id]
            raise
        return trabajo

    def resumen(self, trabajo=None, job_id=None):
        """Resumen del estado de la cola y (opcional) de un trabajo concreto.

        Devuelve {} si el job_id no existe (ni pendiente ni finalizado)."""
        if trabajo is None and job_id is not None:
            with self._lock:
                trabajo = self._jobs.get(job_id)
            if trabajo is None:
                return {}  # job_id desconocido o ya expirado
        en_curso = self._en_curso
        with self._lock:
            resumen = {
                "en_cola": len(self._seq_cola),
                "en_curso": en_curso.job_id if en_curso else None,
                "resultados": len(self._jobs),
            }
        if trabajo is not None:
            if en_curso is not None and en_curso.job_id == trabajo.job_id:
                estado_job, posicion = "en_curso", 0
            elif trabajo.resultado is not None or trabajo.error is not None:
                estado_job = "ok" if trabajo.error is None else "error"
                posicion = 0
            else:
                estado_job = "en_cola"
                with self._lock:
                    try:
                        posicion = self._seq_cola.index(trabajo.seq) + 1
                    except ValueError:
                        posicion = 0
            resumen.update({
                "job_id": trabajo.job_id,
                "tipo": trabajo.tipo,
                "estado": estado_job,
                "posicion": posicion,
                "creado_s": round(time.time() - trabajo.creado),
                "resultado": trabajo.resultado,
                "error": trabajo.error,
            })
        return resumen

    def esperar(self, trabajo, timeout):
        """Espera el resultado del trabajo (modo sincrono). Devuelve True si
        termino; False si se agoto el tiempo."""
        return trabajo.evento.wait(timeout)

    # -- Worker -------------------------------------------------------------

    def _bucle(self):
        while True:
            trabajo = self.cola.get()
            with self._lock:
                try:
                    self._seq_cola.pop(0)
                except IndexError:
                    pass
                self._en_curso = trabajo
            trabajo.iniciado = time.time()
            self.estado["ultima_actividad"] = time.time()
            try:
                LOG.info("[%s] trabajo %s iniciado (cola=%d)", trabajo.tipo,
                         trabajo.job_id, len(self._seq_cola))
                trabajo.resultado = trabajo.fn(self.estado)
            except Exception as exc:
                LOG.exception("[%s] trabajo %s fallo", trabajo.tipo, trabajo.job_id)
                trabajo.error = str(exc)
            finally:
                with self._lock:
                    self._en_curso = None
                self._guardar(trabajo)
                trabajo.evento.set()
                self.estado["ocupado"] = False
                self.cola.task_done()

    def _guardar(self, trabajo):
        """Mantiene acotados los trabajos: descarta los finalizados mas
        antiguos cuando se supera max_resultados (nunca un pendiente)."""
        with self._lock:
            finalizados = [t for t in self._jobs.values()
                           if t.resultado is not None or t.error is not None]
            exceso = len(finalizados) - self.max_resultados
            for t in finalizados[:max(0, exceso)]:
                del self._jobs[t.job_id]


# ---------------------------------------------------------------------------
# Logica de inferencia (funciones puras de trabajo, ejecutadas por el worker)
# ---------------------------------------------------------------------------

def trabajo_ocr(datos):
    def fn(estado):
        lang = datos.get("lang") or estado.get("lang") or "es"
        if lang not in IDIOMAS_VALIDOS:
            raise ValueError(f"lang no soportado: {lang} (use es/pt/en)")
        estado["lang"] = lang
        if estado.get("ocr") is None:
            estado["ocr"] = cargar_modelo_ocr(lang=lang)
        t0 = time.time()
        texts = predecir_textos(estado["ocr"], datos["image"])
        LOG.info("OCR completado en %.1f s (%d regiones)", time.time() - t0, len(texts))
        expected = [str(e) for e in datos.get("expected") or []]
        missing = comprobar_textos(texts, expected)
        return {"ok": True, "texts": texts, "missing": missing,
                "all_found": not missing}
    return fn


def trabajo_ask(datos):
    def fn(estado):
        query = datos.get("query")
        if not query:
            raise ValueError("Falta el campo 'query'")
        engine = datos.get("engine") or estado.get("vision_engine") or "docbee"
        if engine not in MOTORES_VALIDOS:
            raise ValueError(
                f"engine no soportado: {engine} (use paddleocr-vl/docbee/ollama/gemma3)")
        estado["vision_engine"] = engine
        if engine not in ("ollama", "gemma3") and (
                estado.get("vision") is None or estado.get("vision_engine") != engine):
            estado["vision"] = cargar_modelo_vision(engine)
            estado["vision_engine"] = engine
        t0 = time.time()
        answer = preguntar(estado.get("vision"), datos["image"], query, engine=engine)
        LOG.info("Vision (%s) completada en %.1f s", engine, time.time() - t0)
        return {"ok": True, "answer": answer, "engine": engine}
    return fn


def trabajo_chart(datos):
    def fn(estado):
        if estado.get("chart") is None:
            estado["chart"] = cargar_modelo_chart()
        t0 = time.time()
        resultados = estado["chart"].predict({"image": datos["image"]})
        if not resultados:
            raise RuntimeError("No se obtuvo ningun resultado del modelo.")
        df = markdown_a_df(obtener_markdown(resultados[0]))
        LOG.info("Chart completado en %.1f s (%d filas)", time.time() - t0, len(df))
        return {"ok": True, "filas": len(df), "markdown": df_a_markdown(df),
                "csv": df.to_csv(index=False)}
    return fn


def trabajo_vision(datos):
    def fn(estado):
        import vision  # import perezoso: evita el ciclo ocr_server -> vision
        modo = datos.get("modo", "auto")
        if modo not in vision.MODOS:
            raise ValueError(f"modo invalido: {modo} (validos: {vision.MODOS})")
        con_fallback = bool(datos.get("fallback", False))
        LOG.info("Vision modo=%s para: %s", modo, datos["image"])
        return vision.ejecutar(datos["image"], modo, con_fallback)
    return fn


FABRICAS = {
    "ocr": trabajo_ocr,
    "ask": trabajo_ask,
    "chart": trabajo_chart,
    "vision": trabajo_vision,
}


def validar_trabajo(tipo, datos):
    """Validacion rapida de cliente ANTES de encolar (400 inmediato, sin
    tocar la cola ni los modelos). El worker revalida por defensa en
    profundidad, pero los errores de entrada no deben esperar en la cola."""
    if tipo == "ocr":
        lang = datos.get("lang") or "es"
        if lang not in IDIOMAS_VALIDOS:
            return f"lang no soportado: {lang} (use es/pt/en)"
    elif tipo == "ask":
        if not datos.get("query"):
            return "Falta el campo 'query'"
        engine = datos.get("engine") or "docbee"
        if engine not in MOTORES_VALIDOS:
            return (f"engine no soportado: {engine} "
                    f"(use paddleocr-vl/docbee/ollama/gemma3)")
    elif tipo == "vision":
        modo = datos.get("modo", "auto")
        if modo not in MODOS_VISION:
            return f"modo invalido: {modo} (validos: {MODOS_VISION})"
    return None


# ---------------------------------------------------------------------------
# Handler HTTP: acepta al instante, encola, y responde sync/async
# ---------------------------------------------------------------------------

def crear_handler(estado, servicio):
    """Construye la clase del handler con acceso al estado y a la cola."""

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
            if self.path == "/health":
                self._enviar_json(200, {
                    "status": "ok",
                    "modelos": [
                        f"PP-OCRv6 (lang={estado.get('lang', 'es')})",
                        "PP-DocBee (cargado)" if estado.get("vision") is not None else "PP-DocBee (perezoso)",
                        "PP-Chart2Table (cargado)" if estado.get("chart") is not None else "PP-Chart2Table (perezoso)",
                    ],
                    "modos": MODOS_VISION,
                    "ollama": ollama_disponible(),
                    "cola": servicio.resumen(),
                    "uptime_s": round(time.time() - estado["inicio"]),
                })
                return
            if self.path.startswith("/resultado/"):
                job_id = self.path[len("/resultado/"):].strip("/")
                resumen = servicio.resumen(job_id=job_id)
                if "job_id" not in resumen:
                    self._enviar_json(404, {"ok": False,
                                            "error": f"job_id desconocido o expirado: {job_id}"})
                    return
                codigo = 200 if resumen["estado"] in ("ok", "error") else 202
                self._enviar_json(codigo, resumen)
                return
            self._enviar_json(404, {"ok": False,
                                    "error": "Solo GET /health o GET /resultado/<job_id> o POST /ocr, /ask, /chart, /vision"})

        def do_POST(self):
            tipo = self.path[1:]  # "/ask" -> "ask"
            if tipo not in FABRICAS:
                self._enviar_json(404, {"ok": False,
                                        "error": "Solo GET /health o POST /ocr, /ask, /chart, /vision"})
                return

            error, datos = self._recibir_json()
            if error:
                self._enviar_json(error, {"ok": False, "error": datos})
                return
            if not os.path.isfile(datos["image"]):
                self._enviar_json(400, {"ok": False,
                                        "error": f"Imagen no encontrada: {datos['image']}"})
                return

            tipo = self.path[1:]  # ya calculado arriba
            error_validacion = validar_trabajo(tipo, datos)
            if error_validacion:
                self._enviar_json(400, {"ok": False, "error": error_validacion})
                return

            try:
                trabajo = servicio.enviar(tipo, FABRICAS[tipo](datos))
            except queue.Full:
                self._enviar_json(503, {"ok": False,
                                        "error": f"Cola llena ({MAX_COLA} trabajos): reintenta en unos segundos",
                                        "cola": servicio.resumen()})
                return

            estado["ultima_actividad"] = time.time()
            estado["ocupado"] = True

            if datos.get("async"):
                self._enviar_json(202, {"ok": True, "job_id": trabajo.job_id,
                                        "tipo": tipo, "estado": "en_cola",
                                        "cola": servicio.resumen(),
                                        "resultado": "/resultado/" + trabajo.job_id})
                return

            # Modo sincrono: espera acotada; si se agota, 503 con job_id.
            espera = datos.get("espera_s", ESPERA_DEFAULT_S)
            try:
                espera = max(0.0, min(float(espera), ESPERA_DEFAULT_S * 2))
            except (TypeError, ValueError):
                espera = ESPERA_DEFAULT_S
            if not servicio.esperar(trabajo, espera):
                self._enviar_json(503, {
                    "ok": False,
                    "error": f"tiempo de espera agotado tras {espera:.0f}s "
                             f"(la inferencia puede tardar minutos)",
                    "job_id": trabajo.job_id,
                    "cola": servicio.resumen(),
                    "sugerencia": "recupera el resultado con GET /resultado/" + trabajo.job_id,
                })
                return
            resumen = servicio.resumen(trabajo=trabajo)
            if resumen.get("error"):
                self._enviar_json(500, {"ok": False, "error": resumen["error"],
                                        "job_id": trabajo.job_id})
            else:
                self._enviar_json(200, resumen["resultado"])

    return OcrHandler


def main():
    parser = argparse.ArgumentParser(description="Servicio unico de OCR + Vision IA + Chart (con cola)")
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
    servicio = ServicioCola(estado)
    server = HTTPServer((args.host, args.port), crear_handler(estado, servicio))

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
