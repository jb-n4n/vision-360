#!/usr/bin/env python3
"""Tests de los puntuadores de la batería 360° (sin motores VLM)."""

import unittest

from scripts.bateria_360 import (puntuar_etiquetas, puntuar_valores,
                                  extraer_numeros, puntuar_conteo, puntuar)


class TestPuntuadores(unittest.TestCase):

    def test_extraer_numeros(self):
        self.assertEqual(extraer_numeros("35.0% y -2.90 y 104,22"), [35.0, -2.9, 104.22])

    def test_valores_exactos(self):
        a, t = puntuar_valores("104.22 99.11 57.87 -3.87 68.99 -2.9 56.29 -9.48 87.99 5.96 9.87 7.47",
                               [104.22, 99.11, 57.87, -3.87, 68.99, -2.9, 56.29, -9.48, 87.99, 5.96, 9.87, 7.47])
        self.assertEqual((a, t), (12, 12))

    def test_valores_parciales_y_sufijos(self):
        a, t = puntuar_valores("Alpha 35.0%, Beta 25.5%", [35.0, 25.5, 18.2])
        self.assertEqual((a, t), (2, 3))

    def test_etiquetas_case_insensitive(self):
        a, t = puntuar_etiquetas("Veo Banana, Apple y una ORANGE", ["banana", "apple", "orange"])
        self.assertEqual((a, t), (3, 3))

    def test_conteo_dentro_tolerancia(self):
        self.assertEqual(puntuar_conteo("hay 10 personas", "person", 10), (1, 1))
        self.assertEqual(puntuar_conteo("hay 3 personas", "person", 10), (0, 1))
        self.assertEqual(puntuar_conteo("no se cuantos", "person", 10), (0, 1))

    def test_puntuar_objetos(self):
        res = puntuar({"scoring": "objetos",
                       "esperado": {"etiquetas": ["banana", "apple"], "conteo": 3}},
                      "banana apple apple orange x3")
        self.assertEqual(res["tipo"], "objetos+conteo")
        self.assertEqual(res["aciertos"], 3)

    def test_puntuar_libre(self):
        res = puntuar({"scoring": "libre", "esperado": {}}, "cualquier cosa")
        self.assertEqual(res["tipo"], "libre")


if __name__ == "__main__":
    unittest.main()
