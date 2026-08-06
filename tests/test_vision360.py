"""Pruebas unitarias de vision360.py (paquete central vision-360).

Sin paddleocr: solo PIL + lógica pura. Ejecutar desde la raíz:
    .venv-ocr\Scripts\python.exe -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import unittest

import vision360


def _crear_imagen(path, w=200, h=100):
    from PIL import Image

    Image.new("RGB", (w, h), (240, 240, 240)).save(path)
    return path


class TestCargarRegiones(unittest.TestCase):
    def test_valido(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            with open(p, "w") as f:
                json.dump([{"label": "a", "box": [0, 0, 10, 10]}, {"label": "b", "box": [5, 5, 20, 30]}], f)
            regiones = vision360.cargar_regiones(p)
            self.assertEqual(len(regiones), 2)
            self.assertEqual(regiones[1]["label"], "b")

    def test_no_lista(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            with open(p, "w") as f:
                json.dump({"label": "x"}, f)
            with self.assertRaises(ValueError):
                vision360.cargar_regiones(p)

    def test_box_invalido(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.json")
            with open(p, "w") as f:
                json.dump([{"label": "a", "box": [0, 0, "x"]}], f)
            with self.assertRaises(ValueError):
                vision360.cargar_regiones(p)


class TestContarDom(unittest.TestCase):
    def test_conteo(self):
        self.assertEqual(vision360.contar_dom([{}, {}, {}]), 3)
        self.assertEqual(vision360.contar_dom([]), 0)


class TestSomOverlay(unittest.TestCase):
    def test_genera_imagen_con_rectangulos(self):
        with tempfile.TemporaryDirectory() as d:
            img = _crear_imagen(os.path.join(d, "in.png"))
            out = os.path.join(d, "out.png")
            vision360.som_overlay(img, [{"label": "a", "box": [10, 10, 50, 40]}], out)
            self.assertTrue(os.path.exists(out))
            from PIL import Image

            with Image.open(out) as res:
                # El pixel interior del recuadro debe conservar el fondo gris.
                self.assertEqual(res.getpixel((30, 30)), (240, 240, 240))
                # El borde debe tener el color SOM (rojo).
                self.assertEqual(res.getpixel((10, 20)), vision360.SOM_COLOR)


class TestRecortarRegion(unittest.TestCase):
    def test_recorte(self):
        with tempfile.TemporaryDirectory() as d:
            img = _crear_imagen(os.path.join(d, "in.png"), w=200, h=100)
            out = os.path.join(d, "crop.png")
            vision360.recortar_region(img, [10, 10, 50, 30], out)
            from PIL import Image

            with Image.open(out) as res:
                self.assertEqual(res.size, (50, 30))


if __name__ == "__main__":
    unittest.main()
