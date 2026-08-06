"""Pruebas unitarias de extractor_final.py y chart_server.py.

Ejecutar desde la raíz del proyecto:
    python3 -m unittest discover -s tests -v

No requiere paddleocr (se prueba con modelos simulados); solo pandas.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

import pandas as pd

sys.path.insert(0, ".")

import chart_server
from extractor_final import (
    es_archivo_imagen,
    es_fila_separadora,
    markdown_a_df,
    obtener_markdown,
    validar_imagen,
)


class TestFilaSeparadora(unittest.TestCase):
    def test_filas_separadoras(self):
        for linea in [
            "|---|",
            "| --- | --- |",
            "|---|---|",
            "--- | --- | ---",
            "|:---|:---:|---:|",
            "---",
        ]:
            with self.subTest(linea=linea):
                self.assertTrue(es_fila_separadora(linea))

    def test_filas_de_datos(self):
        for linea in [
            "| A | B |",
            "| 2018 | 104.22 | -3.87 |",
            "|---| 1 |",        # celda con guiones mezclada con dato
            "| - | - |",        # guion simple (no separador)
            "|-- 1--| 2 |",
            "| -3.87 | 2 |",
        ]:
            with self.subTest(linea=linea):
                self.assertFalse(es_fila_separadora(linea))


class TestMarkdownADf(unittest.TestCase):
    def test_markdown_con_separador_y_pipes(self):
        md = (
            "| Año | Ventas |\n"
            "| --- | --- |\n"
            "| 2018 | 104.22 |\n"
            "| 2019 | 99.11 |\n"
        )
        df = markdown_a_df(md)
        self.assertEqual(list(df.columns), ["Año", "Ventas"])
        self.assertEqual(len(df), 2)
        # pandas infiere tipos numericos (deseable para extraccion de datos)
        self.assertEqual(float(df.iloc[0]["Ventas"]), 104.22)

    def test_markdown_sin_pipes_iniciales(self):
        md = (
            "Año | Ventas\n"
            "2018 | 104.22\n"
        )
        df = markdown_a_df(md)
        self.assertEqual(list(df.columns), ["Año", "Ventas"])
        self.assertEqual(len(df), 1)

    def test_markdown_con_lineas_vacias(self):
        md = "A | B\n\n2018 | 1\n\n"
        df = markdown_a_df(md)
        self.assertEqual(len(df), 1)

    def test_markdown_vacio_raise(self):
        for md in ["", "   ", "--- | ---\n", "| --- | --- |\n\n"]:
            with self.subTest(md=repr(md)):
                with self.assertRaises(ValueError):
                    markdown_a_df(md)


class TestValidarImagen(unittest.TestCase):
    def test_imagen_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            validar_imagen("no_existe_esta_imagen.png")

    def test_imagen_existente(self):
        self.assertEqual(validar_imagen("ejemplos/grafico_demo.png"), "ejemplos/grafico_demo.png")

    def test_directorio_no_es_imagen(self):
        # Un directorio "existe" pero no es una imagen: no debe pasar la validacion
        # (cargar el modelo para nada costaria 3-5 min y 4.8 GB de RAM).
        with self.assertRaises(ValueError):
            validar_imagen("ejemplos")

    def test_archivo_no_imagen(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"esto no es una imagen, solo texto")
            ruta = f.name
        try:
            with self.assertRaises(ValueError):
                validar_imagen(ruta)
        finally:
            os.unlink(ruta)

    def test_archivo_vacio_no_es_imagen(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            ruta = f.name
        try:
            with self.assertRaises(ValueError):
                validar_imagen(ruta)
        finally:
            os.unlink(ruta)

    def test_firma_magica_png(self):
        self.assertTrue(es_archivo_imagen("ejemplos/grafico_demo.png"))
        self.assertFalse(es_archivo_imagen("requirements.txt"))


class ResultadoFalso:
    """Objeto Result de PaddleX simulado (solo el atributo .json)."""

    def __init__(self, datos):
        self.json = datos


class TestObtenerMarkdown(unittest.TestCase):
    def test_result_en_raiz(self):
        res = ResultadoFalso({"result": "A | B\n1 | 2"})
        self.assertEqual(obtener_markdown(res), "A | B\n1 | 2")

    def test_result_dentro_de_res(self):
        res = ResultadoFalso({"res": {"image": "x.png", "result": "A | B\n1 | 2"}})
        self.assertEqual(obtener_markdown(res), "A | B\n1 | 2")

    def test_result_vacio_o_none_tratado_como_ausente(self):
        for datos in [
            {"result": None},
            {"result": ""},
            {"res": {"result": None}},
            {"res": {"result": ""}},
        ]:
            with self.subTest(datos=datos):
                res = ResultadoFalso(datos)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(KeyError):
                        obtener_markdown(res)

    def test_result_vacio_en_raiz_pero_presente_en_res(self):
        res = ResultadoFalso({"result": None, "res": {"result": "A | B\n1 | 2"}})
        self.assertEqual(obtener_markdown(res), "A | B\n1 | 2")

    def test_sin_result_raise(self):
        res = ResultadoFalso({"otra_cosa": 1})
        with contextlib.redirect_stdout(io.StringIO()):  # silenciar el print de depuracion
            with self.assertRaises(KeyError):
                obtener_markdown(res)


class ModeloFalso:
    """Simula ChartParsing.predict(): devuelve una tabla markdown con separador."""

    def predict(self, entrada):
        time.sleep(0.1)
        return [ResultadoFalso({
            "res": {"image": entrada["image"], "result": "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"}
        })]


class TestChartServer(unittest.TestCase):
    PUERTO = 8124
    BASE = f"http://127.0.0.1:{PUERTO}"

    @classmethod
    def setUpClass(cls):
        cls.modelo = ModeloFalso()
        cls.estado = {"inicio": time.time(), "ultima_actividad": time.time(), "ocupado": False}
        cls.server = HTTPServer(("127.0.0.1", cls.PUERTO), chart_server.crear_handler(cls.modelo, cls.estado))
        cls.hilo = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.hilo.start()
        threading.Thread(target=chart_server.vigia, args=(cls.server, cls.estado, 60), daemon=True).start()

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
        self.assertEqual(cuerpo["modelo"], "PP-Chart2Table")

    def test_chart_ok(self):
        codigo, cuerpo = self._req("/chart", {"image": "ejemplos/grafico_demo.png"})
        self.assertEqual(codigo, 200)
        self.assertTrue(cuerpo["ok"])
        self.assertEqual(cuerpo["filas"], 2)
        self.assertIn("A", cuerpo["csv"])
        self.assertNotIn("---", cuerpo["csv"])  # la fila separadora no llega al CSV
        self.assertIn("| A |", cuerpo["markdown"])

    def test_chart_json_invalido(self):
        codigo, cuerpo = self._req("/chart", {"mal": "json"})
        self.assertEqual(codigo, 400)

    def test_chart_json_valido_sin_clave_image(self):
        # JSON valido pero sin la clave esperada: 400 con mensaje que lo distingue
        # del JSON malformado (no debe decir "JSON invalido").
        codigo, cuerpo = self._req("/chart", {"otra_clave": "x"})
        self.assertEqual(codigo, 400)
        self.assertNotIn("JSON invalido", cuerpo["error"])
        self.assertIn("image", cuerpo["error"])

    def test_chart_image_no_string(self):
        codigo, _ = self._req("/chart", {"image": 12345})
        self.assertEqual(codigo, 400)

    def test_chart_sin_cuerpo(self):
        # POST explicito con cuerpo vacio (un GET a /chart devuelve 404, lo cual es correcto)
        r = urllib.request.Request(
            self.BASE + "/chart",
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

    def test_chart_cuerpo_demasiado_grande(self):
        # Un cuerpo mayor que MAX_CUERPO debe rechazarse con 413 sin procesarse
        r = urllib.request.Request(
            self.BASE + "/chart",
            data=b"x" * (chart_server.MAX_CUERPO + 1),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r) as resp:
                codigo, _ = resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            codigo, _ = e.code, e.read().decode()
        self.assertEqual(codigo, 413)

    def test_ruta_desconocida(self):
        codigo, _ = self._req("/otra")
        self.assertEqual(codigo, 404)

    def test_vision_modo_invalido(self):
        codigo, cuerpo = self._req("/vision", {"image": "x.png", "modo": "nada"})
        self.assertEqual(codigo, 400)
        self.assertIn("modo invalido", cuerpo["error"])

    def test_vision_json_invalido_reutiliza_validacion(self):
        codigo, cuerpo = self._req("/vision", {"mal": "json"})
        self.assertEqual(codigo, 400)
        self.assertIn("clave 'image'", cuerpo["error"])

    def test_vision_ok_con_ejecutar_mockeado(self):
        import unittest.mock as mock

        import vision

        falso = {"ok": True, "detecciones": [{"clase": "person", "score": 0.9}]}
        with mock.patch.object(vision, "ejecutar", return_value=falso) as ejecutar_falso:
            codigo, cuerpo = self._req("/vision", {"image": "foto.png", "modo": "objetos"})
            ejecutar_falso.assert_called_once_with("foto.png", "objetos", False)
        self.assertEqual(codigo, 200)
        self.assertTrue(cuerpo["ok"])
class TestDFAMarkdown(unittest.TestCase):
    def test_celda_multilinea_no_rompe_tabla(self):
        df = pd.DataFrame({"A": ["x\ny"], "B": [1]})
        md = chart_server.df_a_markdown(df)
        lineas = md.splitlines()
        self.assertEqual(len(lineas), 3)  # cabecera + separador + 1 fila
        self.assertIn("| x y | 1 |", lineas[2])


class TestCierrePorInactividad(unittest.TestCase):
    """El servidor debe cerrarse solo tras el tiempo de inactividad."""

    def test_cierre_tras_timeout(self):
        estado = {"inicio": time.time(), "ultima_actividad": time.time(), "ocupado": False}
        server = HTTPServer(("127.0.0.1", 8125), chart_server.crear_handler(ModeloFalso(), estado))
        hilo = threading.Thread(target=server.serve_forever, daemon=True)
        hilo.start()
        threading.Thread(target=chart_server.vigia, args=(server, estado, 2), daemon=True).start()

        time.sleep(4.5)
        hilo.join(timeout=2)
        server.server_close()
        self.assertFalse(hilo.is_alive(), "El servidor debería haberse cerrado por inactividad")


if __name__ == "__main__":
    unittest.main()
