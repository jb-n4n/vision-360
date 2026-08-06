"""Pruebas unitarias de ocr_server.py (paquete central vision-360).

Adaptadas de better-ocr (https://github.com/jmbigi/better-ocr, CC BY-SA 4.0),
mismo patron que test_chart_ocr.py: modelos simulados, sin requerir paddleocr.

Ejecutar desde la raiz del proyecto:
    .venv-ocr\Scripts\python.exe -m unittest discover -s tests -v
"""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from unittest import mock

import ocr_server

IMAGEN = __file__


class ModeloOcrFalso:
    """Simula PP-OCRv6.predict(): devuelve paginas con 'rec_texts'."""

    def __init__(self, textos, lento=0.05):
        self.textos = textos
        self.lento = lento

    def predict(self, image_path):
        time.sleep(self.lento)
        yield {"rec_texts": list(self.textos)}


class ModeloVisionFalso:
    """Simula PaddleOCR-VL.predict(): devuelve bloques 'parsing_res_list'."""

    def __init__(self, respuesta):
        self.respuesta = respuesta

    def predict(self, entrada, **kwargs):
        time.sleep(0.05)
        yield {"parsing_res_list": [{"content": self.respuesta}]}


class ResultadoChartFalso:
    """Objeto Result de PaddleX simulado para ChartParsing (solo .json)."""

    def __init__(self, markdown):
        self.json = {"res": {"image": "x.png", "result": markdown}}


class ModeloChartFalso:
    """Simula ChartParsing.predict(): devuelve una tabla markdown con separador."""

    def predict(self, entrada):
        time.sleep(0.05)
        return [ResultadoChartFalso("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")]


class TestGuardsPrevencion(unittest.TestCase):
    """Guards de prevencion: puerto en uso y dependencia paddleocr."""

    def test_puerto_ocupado_verdadero(self):
        srv = HTTPServer(("127.0.0.1", 8127), lambda *a, **k: None)
        self.addCleanup(srv.server_close)
        self.assertTrue(ocr_server.puerto_ocupado("127.0.0.1", 8127))

    def test_puerto_ocupado_falso(self):
        self.assertFalse(ocr_server.puerto_ocupado("127.0.0.1", 8127))

    def test_verificar_paddleocr_sin_dependencia(self):
        with mock.patch("ocr_server.importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "venv"):
                ocr_server.verificar_paddleocr()

    def test_verificar_paddleocr_ok(self):
        # En el entorno de tests paddleocr puede existir o no: el guard solo
        # falla si NO se encuentra el modulo.
        ocr_server.verificar_paddleocr()


class TestOcrServer(unittest.TestCase):
    PUERTO = 8126
    BASE = f"http://127.0.0.1:{PUERTO}"

    @classmethod
    def setUpClass(cls):
        cls.estado = {
            "ocr": ModeloOcrFalso(["DH001", "Au_PPM", "Perfiles"]),
            "vision": ModeloVisionFalso("Sí, se ven varios paneles."),
            "chart": ModeloChartFalso(),
            "lang": "es",
            "inicio": time.time(),
            "ultima_actividad": time.time(),
            "ocupado": False,
        }
        cls.servicio = ocr_server.ServicioCola(cls.estado)
        cls.server = HTTPServer(("127.0.0.1", cls.PUERTO), ocr_server.crear_handler(cls.estado, cls.servicio))
        cls.hilo = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.hilo.start()
        threading.Thread(target=ocr_server.vigia, args=(cls.server, cls.estado, 60), daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.hilo.join(timeout=2)
        cls.server.server_close()

    def _req(self, ruta, datos=None):
        r = urllib.request.Request(
            self.BASE + ruta,
            data=json.dumps(datos).encode() if datos else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_health(self):
        codigo, cuerpo = self._req("/health")
        self.assertEqual(codigo, 200)
        self.assertEqual(cuerpo["status"], "ok")
        self.assertIn("PP-OCRv6", cuerpo["modelos"][0])
        self.assertIn("PP-DocBee", cuerpo["modelos"][1])
        self.assertIn("graficos", cuerpo["modos"])

    def test_chart_ok(self):
        codigo, cuerpo = self._req("/chart", {"image": IMAGEN})
        self.assertEqual(codigo, 200)
        self.assertTrue(cuerpo["ok"])
        self.assertEqual(cuerpo["filas"], 2)
        self.assertIn("| A |", cuerpo["markdown"])
        self.assertIn("A,B", cuerpo["csv"])

    def test_vision_modo_invalido(self):
        codigo, cuerpo = self._req("/vision", {"image": IMAGEN, "modo": "invalido"})
        self.assertEqual(codigo, 400)
        self.assertIn("modo invalido", cuerpo["error"])

    def test_cuerpo_demasiado_grande(self):
        r = urllib.request.Request(
            self.BASE + "/ocr",
            data=json.dumps({"image": "x" * (ocr_server.MAX_CUERPO + 1024)}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r) as resp:
                codigo, cuerpo = resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            codigo, cuerpo = e.code, json.loads(e.read().decode())
        self.assertEqual(codigo, 413)
        self.assertIn("demasiado grande", cuerpo["error"])

    def test_falta_clave_image(self):
        codigo, cuerpo = self._req("/ocr", {"otra": "clave"})
        self.assertEqual(codigo, 400)
        self.assertIn("image", cuerpo["error"])

    def test_ocr_todos_encontrados(self):
        codigo, cuerpo = self._req("/ocr", {"image": IMAGEN, "expected": ["DH001", "Au_PPM"]})
        self.assertEqual(codigo, 200)
        self.assertTrue(cuerpo["ok"])
        self.assertTrue(cuerpo["all_found"])
        self.assertEqual(cuerpo["missing"], [])

    def test_ocr_con_faltantes(self):
        codigo, cuerpo = self._req("/ocr", {"image": IMAGEN, "expected": ["DH001", "NO_EXISTE"]})
        self.assertEqual(codigo, 200)
        self.assertFalse(cuerpo["all_found"])
        self.assertEqual(cuerpo["missing"], ["NO_EXISTE"])

    def test_ocr_lang_default_es(self):
        self._req("/ocr", {"image": IMAGEN, "expected": []})
        self.assertEqual(self.estado["lang"], "es")

    def test_ocr_lang_soportado(self):
        codigo, cuerpo = self._req("/ocr", {"image": IMAGEN, "lang": "pt", "expected": []})
        self.assertEqual(codigo, 200)
        self.assertEqual(self.estado["lang"], "pt")

    def test_ocr_lang_invalido(self):
        codigo, cuerpo = self._req("/ocr", {"image": IMAGEN, "lang": "xx", "expected": []})
        self.assertEqual(codigo, 400)

    def test_ocr_imagen_inexistente(self):
        codigo, cuerpo = self._req("/ocr", {"image": "no/existe.png"})
        self.assertEqual(codigo, 400)
        self.assertIn("Imagen no encontrada", cuerpo["error"])

    def test_ask(self):
        codigo, cuerpo = self._req("/ask", {"image": IMAGEN, "query": "¿Se ven paneles?", "engine": "paddleocr-vl"})
        self.assertEqual(codigo, 200)
        self.assertTrue(cuerpo["ok"])
        self.assertIn("paneles", cuerpo["answer"])

    def test_ask_engine_invalido(self):
        codigo, cuerpo = self._req("/ask", {"image": IMAGEN, "query": "¿?", "engine": "xx"})
        self.assertEqual(codigo, 400)

    def test_ask_sin_query(self):
        codigo, cuerpo = self._req("/ask", {"image": IMAGEN})
        self.assertEqual(codigo, 400)

    def test_json_invalido(self):
        r = urllib.request.Request(
            self.BASE + "/ocr",
            data=b"",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r) as resp:
                codigo, cuerpo = resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            codigo, cuerpo = e.code, json.loads(e.read().decode())
        self.assertEqual(codigo, 400)

    def test_ruta_desconocida(self):
        codigo, _ = self._req("/otra")
        self.assertEqual(codigo, 404)


class TestColaYAsync(unittest.TestCase):
    """Cola de trabajos: serializacion, /health instantaneo, async + polling.

    Modelos lentos (0.4 s) para poder observar el estado de la cola sin
    depender de la velocidad del host.
    """

    PUERTO = 8128
    BASE = f"http://127.0.0.1:{PUERTO}"

    @classmethod
    def setUpClass(cls):
        cls.estado = {
            "ocr": ModeloOcrFalso(["DH001"], lento=0.4),
            "vision": None,
            "chart": None,
            "lang": "es",
            "inicio": time.time(),
            "ultima_actividad": time.time(),
            "ocupado": False,
        }
        cls.servicio = ocr_server.ServicioCola(cls.estado)
        cls.server = HTTPServer(("127.0.0.1", cls.PUERTO), ocr_server.crear_handler(cls.estado, cls.servicio))
        cls.hilo = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.hilo.join(timeout=2)
        cls.server.server_close()

    def _req(self, ruta, datos=None):
        r = urllib.request.Request(
            self.BASE + ruta,
            data=json.dumps(datos).encode() if datos else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_health_instantaneo_durante_inferencia(self):
        """El servidor HTTP nunca se bloquea con la inferencia: /health
        responde en milisegundos aunque un trabajo lento este en curso."""
        codigo, _ = self._req("/ocr", {"image": IMAGEN, "async": True})
        self.assertEqual(codigo, 202)
        with mock.patch("ocr_server.ollama_disponible", return_value=True):
            t0 = time.time()
            codigo, cuerpo = self._req("/health")
            lapso = time.time() - t0
        self.assertEqual(codigo, 200)
        self.assertEqual(cuerpo["status"], "ok")
        self.assertLess(lapso, 0.3, f"/health tardo {lapso:.2f}s durante la inferencia")

    def test_async_polling_hasta_resultado(self):
        codigo, cuerpo = self._req("/ocr", {"image": IMAGEN, "async": True,
                                            "expected": ["DH001"]})
        self.assertEqual(codigo, 202)
        job_id = cuerpo["job_id"]
        self.assertTrue(job_id)
        self.assertIn("/resultado/", cuerpo["resultado"])

        resultado = None
        for _ in range(40):  # 40 x 0.1s = 4s >> 0.4s del modelo lento
            codigo, cuerpo = self._req("/resultado/" + job_id)
            if codigo == 200:
                resultado = cuerpo
                break
            self.assertIn(cuerpo["estado"], ("en_cola", "en_curso"))
            time.sleep(0.1)
        self.assertIsNotNone(resultado, "el trabajo no termino a tiempo")
        self.assertTrue(resultado["resultado"]["ok"])
        self.assertTrue(resultado["resultado"]["all_found"])

    def test_sync_timeout_503_con_job_id(self):
        """Espera sincrona agotada -> 503 con job_id recuperable por polling."""
        # Ocupa el worker con un trabajo lento primero.
        self._req("/ocr", {"image": IMAGEN, "async": True})
        codigo, cuerpo = self._req("/ocr", {"image": IMAGEN, "espera_s": 0.05})
        self.assertEqual(codigo, 503)
        self.assertIn("job_id", cuerpo)
        self.assertIn("/resultado/", cuerpo["sugerencia"])

        # Recuperacion: el trabajo termina y /resultado lo devuelve.
        for _ in range(40):
            codigo, cuerpo = self._req("/resultado/" + cuerpo["job_id"])
            if codigo == 200:
                self.assertTrue(cuerpo["resultado"]["ok"])
                return
            time.sleep(0.1)
        self.fail("el trabajo 503 no se recupero por polling")

    def test_resultado_desconocido_404(self):
        codigo, _ = self._req("/resultado/inexistente")
        self.assertEqual(codigo, 404)


class TestHandleError(unittest.TestCase):
    """handle_error: resets de conexion -> 1 linea INFO; otros errores -> ERROR."""

    @staticmethod
    def _instancia():
        estado = {"lang": "es", "inicio": time.time(),
                  "ultima_actividad": time.time(), "ocupado": False}
        servicio = ocr_server.ServicioCola(estado)
        return object.__new__(ocr_server.crear_handler(estado, servicio))

    def test_reset_loguea_info_una_linea(self):
        h = self._instancia()
        with mock.patch("ocr_server.LOG") as log:
            try:
                raise ConnectionResetError(10054, "test")
            except ConnectionResetError:
                h.handle_error("sock", ("127.0.0.1", 1))
        log.info.assert_called_once()
        log.error.assert_not_called()
        self.assertIn("cerro la conexion", log.info.call_args[0][0])

    def test_error_generico_loguea_error(self):
        h = self._instancia()
        with mock.patch("ocr_server.LOG") as log:
            try:
                raise ValueError("boom")
            except ValueError:
                h.handle_error("sock", ("127.0.0.1", 1))
        log.error.assert_called_once()
        log.info.assert_not_called()


class TestServicioCola(unittest.TestCase):
    """Tests unitarios de la cola (sin HTTP): serializacion y espera."""

    def test_serializa_trabajos(self):
        estado = {"ultima_actividad": time.time(), "ocupado": False}
        servicio = ocr_server.ServicioCola(estado)
        orden = []

        def fabrica(n):
            def fn(_estado):
                time.sleep(0.05)
                orden.append(n)
                return {"n": n}
            return fn

        jobs = [servicio.enviar("t", fabrica(n)) for n in range(3)]
        self.assertEqual(len(jobs), 3)
        for j in jobs:
            self.assertTrue(servicio.esperar(j, 5))
        self.assertEqual(orden, [0, 1, 2])  # FIFO y serializado

    def test_espera_agotada_devuelve_false(self):
        estado = {"ultima_actividad": time.time(), "ocupado": False}
        servicio = ocr_server.ServicioCola(estado)
        j = servicio.enviar("t", lambda e: (time.sleep(0.2), {"ok": True})[1])
        self.assertFalse(servicio.esperar(j, 0.05))
        self.assertTrue(servicio.esperar(j, 5))
        self.assertEqual(servicio.resumen(trabajo=j)["estado"], "ok")

    def test_estado_muestra_cola_y_resultado(self):
        estado = {"ultima_actividad": time.time(), "ocupado": False}
        servicio = ocr_server.ServicioCola(estado)
        j = servicio.enviar("ocr", lambda e: {"ok": True, "texts": ["a"]})
        self.assertTrue(servicio.esperar(j, 5))
        resumen = servicio.resumen(job_id=j.job_id)
        self.assertEqual(resumen["estado"], "ok")
        self.assertEqual(resumen["tipo"], "ocr")
        self.assertEqual(resumen["resultado"]["texts"], ["a"])
        self.assertIn("en_cola", resumen)
        self.assertIn("en_curso", resumen)

    def test_resultados_acotados(self):
        estado = {"ultima_actividad": time.time(), "ocupado": False}
        servicio = ocr_server.ServicioCola(estado, max_resultados=2)
        jobs = [servicio.enviar("t", lambda e, i=i: {"ok": True, "i": i}) for i in range(3)]
        for j in jobs:
            self.assertTrue(servicio.esperar(j, 5))
        self.assertEqual(servicio.resumen(job_id=jobs[0].job_id), {})  # expirado
        self.assertIn("job_id", servicio.resumen(job_id=jobs[2].job_id))


if __name__ == "__main__":
    unittest.main()
