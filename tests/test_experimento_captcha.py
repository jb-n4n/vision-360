#!/usr/bin/env python3
"""Tests del experimento captcha: bucle de reintentos del reto real.
Solo funciones puras (sin playwright, sin daemon, sin red)."""

import unittest

from scripts.experimento_captcha import _loop_reto


class TestLoopReto(unittest.TestCase):

    def test_exito_en_la_primera_ronda(self):
        rondas, esperas = [], []
        superado = _loop_reto(3, ronda=lambda: rondas.append(1) or True,
                              esperar=lambda: esperas.append(1))
        self.assertTrue(superado)
        self.assertEqual(len(rondas), 1)
        self.assertEqual(len(esperas), 0)

    def test_exito_en_la_segunda_ronda(self):
        rondas, esperas = [], []
        superado = _loop_reto(3, ronda=lambda: rondas.append(1) or len(rondas) == 2,
                              esperar=lambda: esperas.append(1))
        self.assertTrue(superado)
        self.assertEqual(len(rondas), 2)
        self.assertEqual(len(esperas), 1)

    def test_rechazo_total_con_reintentos(self):
        rondas, esperas = [], []
        superado = _loop_reto(3, ronda=lambda: rondas.append(1) or False,
                              esperar=lambda: esperas.append(1))
        self.assertFalse(superado)
        self.assertEqual(len(rondas), 3)
        self.assertEqual(len(esperas), 2)

    def test_un_intento_sin_reintento(self):
        rondas, esperas = [], []
        superado = _loop_reto(1, ronda=lambda: rondas.append(1) or False,
                              esperar=lambda: esperas.append(1))
        self.assertFalse(superado)
        self.assertEqual(len(rondas), 1)
        self.assertEqual(len(esperas), 0)


if __name__ == "__main__":
    unittest.main()
