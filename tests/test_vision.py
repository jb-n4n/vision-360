#!/usr/bin/env python3
"""Tests de vision.py: clasificador puro y formato de salida (sin modelos).

Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest
from unittest import mock

from vision import (clasificar, _salida, cargar_perfil, chequear_ram, ejecutar)
import pandas as pd


def caja(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


class TestClasificar(unittest.TestCase):

    def construir_grafico(self):
        textos, polis = [], []
        for i, anio in enumerate(range(2018, 2024)):
            x = 80 + i * 120
            textos += [str(anio), "104.22", "9.87"]
            polis += [caja(x, 500, x + 50, 520),
                      caja(x - 30, 100, x - 10, 120),
                      caja(x + 30, 200, x + 50, 220)]
        return textos, polis

    def test_grafico_detectado(self):
        textos, polis = self.construir_grafico()
        modo, _ = clasificar(textos, polis, ancho=800, alto=600)
        self.assertEqual(modo, "graficos")

    def test_documento_denso(self):
        textos = [f"línea {i}" for i in range(60)]
        polis = [caja(50, i * 9, 700, i * 9 + 6) for i in range(60)]
        modo, _ = clasificar(textos, polis, ancho=800, alto=600)
        self.assertEqual(modo, "doc")

    def test_foto_sin_texto(self):
        modo, motivo = clasificar(["", ""], [caja(0, 0, 5, 5), caja(6, 6, 10, 10)],
                                  ancho=800, alto=600)
        self.assertEqual(modo, "objetos")
        self.assertIn("2 líneas", motivo)

    def test_texto_disperso(self):
        textos = [f"palabra {i}" for i in range(8)]
        polis = [caja(100, i * 60, 400, i * 60 + 20) for i in range(8)]
        modo, _ = clasificar(textos, polis, ancho=800, alto=600)
        self.assertEqual(modo, "texto")

    def test_sin_lineas_es_objetos(self):
        modo, _ = clasificar([], [], ancho=800, alto=600)
        self.assertEqual(modo, "objetos")


class TestPerfil(unittest.TestCase):

    def test_perfil_ligero_bloquea_doc(self):
        with mock.patch.dict("os.environ", {"BETTER_OCR_PERFIL": "ligero"}):
            perfil = cargar_perfil()
        self.assertEqual(perfil["nombre"], "ligero")
        self.assertEqual(perfil["ram_max_mb"], 3500)
        error = chequear_ram("doc", perfil)
        self.assertIsNotNone(error)
        self.assertIn("limite del perfil", error["error"])
        self.assertIsNone(chequear_ram("objetos", perfil))

    def test_perfil_completo_sin_limite(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            perfil = cargar_perfil()
        self.assertEqual(perfil["nombre"], "completo")
        self.assertIsNone(perfil["ram_max_mb"])
        self.assertIsNone(chequear_ram("doc", perfil))

    def test_ejecutar_bloqueado_por_perfil(self):
        with mock.patch.dict("os.environ", {"BETTER_OCR_PERFIL": "ligero"}):
            resultado = ejecutar("x.png", "doc", con_fallback=False)
        self.assertFalse(resultado["ok"])
        self.assertIn("limite del perfil", resultado["error"])
        self.assertEqual(resultado["perfil"]["nombre"], "ligero")


class TestSalida(unittest.TestCase):

    def test_csv(self):
        datos = {"ok": True, "columnas": ["a", "b"], "tabla": [["1", "2"]]}
        salida = _salida(datos, "csv")
        self.assertIn("a,b", salida)

    def test_markdown(self):
        datos = {"ok": True, "columnas": ["a", "b"], "tabla": [["1", "2"]]}
        salida = _salida(datos, "md")
        self.assertIn("---", salida)
        self.assertIn("| 1 | 2 |", salida)


if __name__ == "__main__":
    unittest.main()
