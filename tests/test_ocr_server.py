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

import ocr_server

IMAGEN = __file__


class ModeloOcrFalso:
    """Simula PP-OCRv6.predict(): devuelve paginas con 'rec_texts'."""

    def __init__(self, textos):
        self.textos = textos

    def predict(self, image_path):
        time.sleep(0.05)
        yield {"rec_texts": list(self.textos)}


class ModeloVisionFalso:
    """Simula PaddleOCR-VL.predict(): devuelve bloques 'parsing_res_list'."""

    def __init__(self, respuesta):
        self.respuesta = respuesta

    def predict(self, entrada, **kwargs):
        time.sleep(0.05)
        yield {"parsing_res_list": [{"content": self.respuesta}]}


class TestOcrServer(unittest.TestCase):
    PUERTO = 8126
    BASE = f"http://127.0.0.1:{PUERTO}"

    @classmethod
    def setUpClass(cls):
        cls.estado = {
            "ocr": ModeloOcrFalso(["DH001", "Au_PPM", "Perfiles"]),
            "vision": ModeloVisionFalso("Sí, se ven varios paneles."),
            "lang": "es",
            "inicio": time.time(),
            "ultima_actividad": time.time(),
            "ocupado": False,
        }
        cls.server = HTTPServer(("127.0.0.1", cls.PUERTO), ocr_server.crear_handler(cls.estado))
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


if __name__ == "__main__":
    unittest.main()
