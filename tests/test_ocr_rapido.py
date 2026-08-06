#!/usr/bin/env python3
"""Tests del emparejamiento geométrico de ocr_rapido (modelo OCR simulado,
sin paddleocr). Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest

from ocr_rapido import LineaTexto, combinar_lineas, emparejar, interseccion, limpiar_token


def caja(x1, y1, x2, y2):
    """Polígono cuadrilátero [x1,y1]-[x2,y2]."""
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


class TestEmparejar(unittest.TestCase):

    def construir_demo(self):
        """Réplica del demo oficial: 6 años y 2 valores por año."""
        textos, polis = [], []
        for i, anio in enumerate(range(2018, 2024)):
            x = 80 + i * 120
            textos += [str(anio), "104.22", "9.87"]
            polis += [caja(x, 500, x + 50, 520),
                      caja(x - 30, 100, x - 10, 120),
                      caja(x + 30, 200, x + 50, 220)]
        return textos, polis

    def test_demo_completo_6x2(self):
        textos, polis = self.construir_demo()
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertTrue(res.ok, res.motivo)
        self.assertIsNotNone(res.df)
        self.assertEqual(res.df.shape[0], 6)
        self.assertEqual(res.df.shape[1], 3)

    def test_pocos_anios_falla(self):
        textos, polis = ["2018", "2019"], [caja(0, 0, 10, 10), caja(20, 0, 30, 10)]
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertFalse(res.ok)
        self.assertIn("pocas categorías", res.motivo)

    def test_anios_no_consecutivos_falla(self):
        textos = ["2018", "2020", "2021"]
        polis = [caja(0, 0, 10, 10), caja(20, 0, 30, 10), caja(40, 0, 50, 10)]
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertFalse(res.ok)
        self.assertIn("no consecutivas", res.motivo)

    def test_anio_sin_valores_falla(self):
        textos, polis = [], []
        for i, anio in enumerate(range(2018, 2024)):
            x = 80 + i * 120
            textos.append(str(anio))
            polis.append(caja(x, 500, x + 50, 520))
            if i != 3:  # 2021 sin valores
                textos += ["10.5", "20.5"]
                polis += [caja(x - 30, 100, x - 10, 120),
                          caja(x + 30, 200, x + 50, 220)]
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertFalse(res.ok)
        self.assertIn("sin valores", res.motivo)

    def test_ticks_del_eje_y_excluidos(self):
        """Valores lejos en X de los años (ticks del eje Y) no se emparejan."""
        textos, polis = self.construir_demo()
        # ticks del eje Y en x=10 (lejos de los años en x>=80)
        for v in ("120", "90", "50"):
            textos.append(v)
            polis.append(caja(10, 100, 20, 120))
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertTrue(res.ok, res.motivo)
        # el conteo por año debe seguir siendo 2
        ncols = [len(r) for r in res.df.astype(str).values]
        self.assertEqual(ncols, [3, 3, 3, 3, 3, 3])

    def test_anio_pegado_a_texto(self):
        """OCR pegó el año a otra palabra ('份2018')."""
        textos, polis = self.construir_demo()
        textos[0] = "份2018"
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertTrue(res.ok, res.motivo)
        self.assertEqual(str(res.df.iloc[0, 0]), "2018")

    def test_valor_negativo_conserva_signo(self):
        """'3.87' leído de una etiqueta '-3.87' debe conservar el menos."""
        textos, polis = self.construir_demo()
        textos[1] = "-3.87"  # primer valor de 2018
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertTrue(res.ok, res.motivo)
        self.assertEqual(str(res.df.iloc[0, 1]), "-3.87")

    def test_valor_menos_pegado(self):
        """'-2.90' del OCR debe compararse bien con -2.9."""
        textos, polis = self.construir_demo()
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertTrue(res.ok, res.motivo)
        v = float(res.df.iloc[1, 2])
        self.assertAlmostEqual(v, 9.87, places=2)

    def test_consistencia_columnas_falla(self):
        """Año con menos valores que el resto = lectura incompleta = fallo."""
        textos, polis = self.construir_demo()
        textos.append("-2.9")  # sexto valor suelto para 2023 -> 3 columnas en 2023
        polis.append(caja(80 + 5 * 120, 300, 80 + 5 * 120 + 20, 320))
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertFalse(res.ok)
        self.assertIn("inconsistente", res.motivo)

    def test_valores_ordenados_por_x(self):
        """Las columnas se ordenan de izquierda a derecha (ingresos, beneficios)."""
        textos, polis = self.construir_demo()
        res = emparejar(textos, polis, ancho_imagen=800)
        self.assertEqual(str(res.df.iloc[0, 1]), "104.22")
        self.assertEqual(str(res.df.iloc[0, 2]), "9.87")

    def test_anio_duplicado_gana_mejor_score(self):
        """Ruido del OCR ('3030' score bajo) en la misma zona que '2020'."""
        textos, polis = self.construir_demo()
        # candidato ruidoso en la zona de 2020 (x = 80+2*120 = 320)
        textos.append("3030")
        polis.append(caja(310, 882, 400, 900))
        scores = [1.0] * (len(textos) - 1) + [0.66]
        res = emparejar(textos, polis, ancho_imagen=800, scores=scores)
        self.assertTrue(res.ok, res.motivo)
        self.assertEqual(str(res.df.iloc[2, 0]), "2020")


class TestLimpiarToken(unittest.TestCase):

    def test_conserva_signo_negativo(self):
        self.assertEqual(limpiar_token("-3.87"), "-3.87")
        self.assertEqual(limpiar_token("-2.9"), "-2.9")

    def test_quita_guiones_colgantes(self):
        self.assertEqual(limpiar_token("50-"), "50")
        self.assertEqual(limpiar_token("90 -"), "90")
        self.assertEqual(limpiar_token(" 120 - "), "120")

    def test_comas_a_puntos(self):
        self.assertEqual(limpiar_token("-2,90"), "-2.90")


class TestCombinarLineas(unittest.TestCase):

    def caja(self, x1, y1, x2, y2):
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    def test_banda_gana_en_solapamiento(self):
        principal = [LineaTexto("3oao", self.caja(560, 890, 620, 920), 0.5)]
        banda = [LineaTexto("2020", self.caja(560, 890, 620, 920), 0.9)]
        res = combinar_lineas(principal, banda)
        self.assertEqual([l.texto for l in res], ["2020"])

    def test_solo_principal_se_mantiene(self):
        principal = [LineaTexto("-9.48", self.caja(1000, 800, 1080, 830), 0.8)]
        res = combinar_lineas(principal, [])
        self.assertEqual([l.texto for l in res], ["-9.48"])

    def test_banda_anade_faltantes(self):
        principal = [LineaTexto("2018", self.caja(146, 890, 200, 920), 0.9)]
        banda = [LineaTexto("2021", self.caja(802, 890, 860, 920), 0.9)]
        res = combinar_lineas(principal, banda)
        self.assertEqual(sorted(l.texto for l in res), ["2018", "2021"])

    def test_duplicado_texto_igual_centros_cercanos(self):
        """Misma etiqueta en ambas pasadas con cajas de distinto tamaño."""
        principal = [LineaTexto("2018", self.caja(146, 890, 210, 930), 0.9)]
        banda = [LineaTexto("2018", self.caja(150, 892, 200, 918), 0.95)]
        res = combinar_lineas(principal, banda)
        self.assertEqual([l.texto for l in res], ["2018"])

    def test_iou_cajas(self):
        a = [0, 0, 10, 10]
        b = [5, 5, 15, 15]
        c = [100, 100, 110, 110]
        self.assertAlmostEqual(interseccion(a, b), 25 / 175, places=3)
        self.assertEqual(interseccion(a, c), 0.0)


if __name__ == "__main__":
    unittest.main()
