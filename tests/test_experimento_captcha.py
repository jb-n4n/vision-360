#!/usr/bin/env python3
"""Tests del experimento captcha: bucle de reintentos y reintento por celda.
Solo funciones puras o con inyeccion/mock (sin playwright, sin daemon, sin red)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.experimento_captcha import (_loop_reto, _llamar_con_reintentos,
                                         _celdas_detector,
                                         _celdas_detector_por_celda,
                                         _instruccion_a_objeto)


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


class TestMapeoCeldas(unittest.TestCase):
    """Regresion: el mapeo bbox->celda (pasada completa) usa coordenadas de la
    imagen completa; la pasada por celda asigna SIEMPRE al numero del loop."""

    def test_mapeo_bbox_a_celda_cuadricula_completa(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            grid = Path(d) / "grid.png"
            Image.new("RGB", (90, 90), "white").save(grid)
            detecciones = [
                {"clase": "bus", "score": 0.9, "bbox": [10, 10, 20, 20]},
                {"clase": "bus", "score": 0.9, "bbox": [40, 40, 50, 50]},
                {"clase": "bus", "score": 0.9, "bbox": [70, 70, 80, 80]},
                {"clase": "bus", "score": 0.3, "bbox": [30, 30, 40, 40]},
                {"clase": "person", "score": 0.9, "bbox": [30, 0, 40, 10]},
            ]
            with mock.patch("scripts.experimento_captcha._post",
                            return_value={"detecciones": detecciones}):
                celdas = _celdas_detector(grid, clase="bus", n=3, umbral=0.6)
            # 1, 5 y 9 mapeadas por centro del bbox; 0.3 fuera de umbral y
            # clase distinta descartadas.
            self.assertEqual(celdas, {1: [("bus", 0.9)], 5: [("bus", 0.9)],
                                      9: [("bus", 0.9)]})

    def test_por_celda_asigna_al_numero_del_loop(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            grid = Path(d) / "grid.png"
            Image.new("RGB", (60, 60), "white").save(grid)
            llamadas = []

            def _post_fake(ruta, datos):
                llamadas.append(1)
                # La posicion del bbox DENTRO del recorte no cambia la celda:
                # la pasada por celda asigna al numero del loop.
                if len(llamadas) == 3:
                    return {"detecciones": [{"clase": "bus", "score": 0.8,
                                             "bbox": [2, 2, 8, 8]}]}
                if len(llamadas) == 4:
                    return {"detecciones": [{"clase": "bus", "score": 0.8,
                                             "bbox": [52, 52, 58, 58]}]}
                return {"detecciones": []}

            with mock.patch("scripts.experimento_captcha._post",
                            side_effect=_post_fake), \
                 mock.patch("scripts.experimento_captcha.time.sleep"):
                celdas = _celdas_detector_por_celda(grid, clase="bus", n=2)
            self.assertEqual(celdas, {3: [("bus", 0.8)], 4: [("bus", 0.8)]})


class TestInstruccionAObjeto(unittest.TestCase):
    """Regresion del parser de instrucciones con cadenas REALES del demo
    (leccion 18): prefijos, articulos, plurales irregulares, texto
    concatenado sin espacio y flag de skip."""

    def test_buses_concatenado_con_skip(self):
        self.assertEqual(
            _instruccion_a_objeto(
                "Select all squares with busesIf there are none, click skip"),
            ("bus", "bus", True))

    def test_cars_concatenado_sin_skip(self):
        self.assertEqual(
            _instruccion_a_objeto(
                "Select all images with carsClick verify once there are none left"),
            ("car", "car", False))

    def test_bicycles_concatenado(self):
        self.assertEqual(
            _instruccion_a_objeto(
                "Select all images with bicyclesClick verify once there are none left"),
            ("bicycle", "bicycle", False))

    def test_articulo_a_fire_hydrant(self):
        self.assertEqual(
            _instruccion_a_objeto("Select all squares with a fire hydrant"),
            ("fire hydrant", "fire hydrant", False))

    def test_traffic_lights_singularizado(self):
        self.assertEqual(
            _instruccion_a_objeto(
                "Select all images with traffic lightsIf there are none, click skip"),
            ("traffic light", "traffic light", True))

    def test_crosswalks_solo_vlm(self):
        self.assertEqual(
            _instruccion_a_objeto(
                "Select all tiles with crosswalksIf there are none, click skip"),
            ("crosswalk", None, True))

    def test_motorcycles_plural_regular(self):
        self.assertEqual(
            _instruccion_a_objeto("Select all squares with motorcycles"),
            ("motorcycle", "motorcycle", False))

    def test_stop_signs_plural_compuesto(self):
        self.assertEqual(
            _instruccion_a_objeto("Select all images with stop signs"),
            ("stop sign", "stop sign", False))

    def test_solo_mensaje_de_skip(self):
        self.assertEqual(
            _instruccion_a_objeto("If there are none, click skip"),
            (None, None, True))

    def test_vacio(self):
        self.assertEqual(_instruccion_a_objeto(""), (None, None, False))


if __name__ == "__main__":
    unittest.main()
