#!/usr/bin/env python3
"""Tests del experimento captcha: bucle de reintentos y reintento por celda.
Solo funciones puras o con inyeccion/mock (sin playwright, sin daemon, sin red)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.experimento_captcha import (_loop_reto, _llamar_con_reintentos,
                                         _celdas_detector_por_celda)


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


class TestLlamarConReintentos(unittest.TestCase):

    def test_ok_al_primer_intento(self):
        llamadas = []

        def llamar():
            llamadas.append(1)
            return {"detecciones": []}

        self.assertEqual(_llamar_con_reintentos(llamar, num=1, espera_seg=0),
                         {"detecciones": []})
        self.assertEqual(len(llamadas), 1)

    def test_ok_al_segundo_intento(self):
        llamadas = []

        def llamar():
            llamadas.append(1)
            if len(llamadas) == 1:
                raise ConnectionResetError("daemon reiniciado")
            return {"detecciones": [{"clase": "bus", "score": 0.9, "bbox": [0, 0, 10, 10]}]}

        res = _llamar_con_reintentos(llamar, num=2, espera_seg=0)
        self.assertEqual(res["detecciones"][0]["clase"], "bus")
        self.assertEqual(len(llamadas), 2)

    def test_todos_fallan_devuelve_none(self):
        llamadas = []

        def llamar():
            llamadas.append(1)
            raise ConnectionResetError("daemon caido")

        self.assertIsNone(_llamar_con_reintentos(llamar, num=3, espera_seg=0))
        self.assertEqual(len(llamadas), 2)  # intentos por defecto

    def test_intentos_personalizados(self):
        llamadas = []

        def llamar():
            llamadas.append(1)
            raise ConnectionResetError("daemon caido")

        self.assertIsNone(_llamar_con_reintentos(llamar, num=4, intentos=3,
                                                 espera_seg=0))
        self.assertEqual(len(llamadas), 3)


class TestCeldaPorCeldaConReintento(unittest.TestCase):
    """Cableado: la pasada por celda usa _llamar_con_reintentos."""

    def test_celda_recuperada_tras_reinicio_del_daemon(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            grid = Path(d) / "grid.png"
            Image.new("RGB", (60, 60), "white").save(grid)
            fallos = [ConnectionResetError("daemon reiniciado a mitad del loop")]

            def _post_fake(ruta, datos):
                if fallos:
                    raise fallos.pop()
                return {"detecciones": [{"clase": "person", "score": 0.8,
                                         "bbox": [5, 5, 15, 15]}]}

            with mock.patch("scripts.experimento_captcha._post",
                            side_effect=_post_fake), \
                 mock.patch("scripts.experimento_captcha.time.sleep"):
                celdas = _celdas_detector_por_celda(grid, clase="person", n=1)
            self.assertEqual(celdas, {1: [("person", 0.8)]})


if __name__ == "__main__":
    unittest.main()
