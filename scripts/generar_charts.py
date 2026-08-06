#!/usr/bin/env python3
"""Genera gráficos de prueba con datos CONOCIDOS (ground truth) y varios
motores de plotting, para validar la cascada de extracción.

Cada gráfico se guarda como PNG en ejemplos/test_charts/ junto a su CSV
de referencia (label, valor1, valor2, ...) que sirve de ground truth.

Motores usados: matplotlib, seaborn, plotly (export estático con kaleido).
Ejecutar con el venv del proyecto (tienen instaladas las librerías):
    /home/admin/venvs/paddle312/bin/python scripts/generar_charts.py
"""

import csv
import os

import matplotlib
matplotlib.use("Agg")  # sin display

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

# Tipografía legible para OCR (xtick por defecto 10pt es demasiado pequeño)
plt.rcParams.update({
    "font.size": 13,
    "axes.labelsize": 14,
    "axes.titlesize": 15,
    "xtick.labelsize": 14,
    "ytick.labelsize": 12,
    "legend.fontsize": 12,
})

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "ejemplos", "test_charts")

ANIOS = ["2018", "2019", "2020", "2021", "2022", "2023"]
REVENUE = [104.22, 99.11, 57.87, 68.99, 56.29, 87.99]
PROFIT = [9.87, 7.47, -3.87, -2.9, -9.48, 5.96]


def guardar(fig, nombre: str, filas: list[list]) -> None:
    """Guarda la figura y su CSV de referencia."""
    fig.tight_layout()
    fig.savefig(os.path.join(DIR, nombre), dpi=150)
    plt.close(fig)
    with open(os.path.join(DIR, nombre.replace(".png", ".csv")), "w",
              newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(filas)
    print(f"generado: {nombre}  ({len(filas)-1} filas de referencia)")


def bar_2series() -> None:
    """Réplica del demo oficial: 2 series × 6 años."""
    x = np.arange(len(ANIOS))
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.bar(x - 0.2, REVENUE, width=0.4, label="Revenue (M)")
    ax.bar(x + 0.2, PROFIT, width=0.4, label="Profit (M)")
    for i, (r, p) in enumerate(zip(REVENUE, PROFIT)):
        ax.text(i - 0.2, r + 2, f"{r}", ha="center", fontsize=11)
        # los negativos se etiquetan DEBAJO de la barra: un offset positivo
        # los acercaría a la línea del eje y el OCR los leería mal
        ax.text(i + 0.2, p - 2 if p < 0 else p + 2, f"{p}",
                ha="center", fontsize=11)
    ax.set_xticks(x, ANIOS)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.legend()
    ax.set_title("Revenue and Profit (M)")
    fig.subplots_adjust(bottom=0.12)  # separa etiquetas de valor y años
    filas = [["anio", "revenue", "profit"]] + [
        [a, r, p] for a, r, p in zip(ANIOS, REVENUE, PROFIT)]
    guardar(fig, "bar_2series.png", filas)


def line_3series() -> None:
    meses = [f"M{i}" for i in range(1, 9)]
    rng = np.random.default_rng(7)
    s1 = [10, 12, 11, 15, 14, 18, 17, 21]
    s2 = [5, 6, 8, 7, 9, 8, 11, 10]
    s3 = list(np.round(rng.uniform(20, 30, 8), 2))
    fig, ax = plt.subplots(figsize=(9, 6))
    for serie, vals, color in (("A", s1, "tab:blue"), ("B", s2, "tab:orange"),
                               ("C", s3, "tab:green")):
        ax.plot(meses, vals, marker="o", label=serie)
        for x, v in zip(meses, vals):
            ax.text(x, v + 0.5, f"{v}", ha="center", fontsize=8)
    ax.legend()
    ax.set_title("Three Series Line Chart")
    # Referencia en el formato del VLM: filas = series, columnas = meses
    filas = [["serie"] + meses, ["A"] + s1, ["B"] + s2, ["C"] + s3]
    guardar(fig, "line_3series.png", filas)


def pie_5() -> None:
    etiquetas = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    valores = [35.0, 25.5, 18.2, 13.3, 8.0]
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie(valores, labels=etiquetas, autopct="%1.1f%%", startangle=90)
    ax.set_title("Pie Chart")
    filas = [["etiqueta", "valor"]] + list(zip(etiquetas, valores))
    guardar(fig, "pie_5.png", filas)


def bar_apilada() -> None:
    trimestres = ["Q1", "Q2", "Q3", "Q4"]
    comp = {"Part A": [20, 25, 22, 28],
            "Part B": [10, 12, 15, 11],
            "Part C": [5, 7, 6, 9]}
    fig, ax = plt.subplots(figsize=(9, 6))
    bottom = np.zeros(4)
    for nombre, vals in comp.items():
        ax.bar(trimestres, vals, bottom=bottom, label=nombre)
        for x, v in zip(range(4), vals):
            ax.text(x, bottom[x] + v / 2, f"{v}", ha="center", fontsize=8)
        bottom += vals
    ax.legend()
    ax.set_title("Stacked Bar Chart")
    # Referencia en el formato del VLM: filas = series, columnas = trimestres
    filas = [["parte"] + trimestres] + [
        [nombre] + comp[nombre] for nombre in comp]
    guardar(fig, "bar_apilada.png", filas)


def scatter_valores() -> None:
    rng = np.random.default_rng(42)
    x = rng.uniform(1, 10, 6)
    y = rng.uniform(5, 25, 6)
    etiquetas = [f"{round(xi,1)},{round(yi,1)}" for xi, yi in zip(x, y)]
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(x, y, s=80)
    for xi, yi, lab in zip(x, y, etiquetas):
        ax.annotate(lab, (xi, yi), textcoords="offset points", xytext=(8, 8),
                    fontsize=8)
    ax.set_title("Scatter Chart")
    ax.set_xlabel("X axis"); ax.set_ylabel("Y axis")
    filas = [["punto", "x", "y"]] + [
        [f"P{i+1}", round(xi, 1), round(yi, 1)] for i, (xi, yi) in enumerate(zip(x, y))]
    guardar(fig, "scatter_valores.png", filas)


def seaborn_agrupado() -> None:
    rng = np.random.default_rng(3)
    categorias = ["Cat A", "Cat B", "Cat C", "Cat D"]
    series = ["S1", "S2"]
    datos = []
    for c in categorias:
        for s in series:
            datos.append({"categoria": c, "serie": s,
                          "valor": round(float(rng.uniform(5, 40, 1)[0]), 2)})
    df = pd.DataFrame(datos)
    fig, ax = plt.subplots(figsize=(9, 6))
    sns.barplot(data=df, x="categoria", y="valor", hue="serie", ax=ax)
    ax.bar_label(ax.containers[0], fontsize=10)
    ax.bar_label(ax.containers[1], fontsize=10)
    ax.set_title("Seaborn Grouped Bar")
    filas = [["categoria", "S1", "S2"]]
    for c in categorias:
        fila = [c]
        for s in series:
            fila.append(float(df[(df.categoria == c) & (df.serie == s)].valor.iloc[0]))
        filas.append(fila)
    guardar(fig, "seaborn_agrupado.png", filas)


def plotly_barra() -> None:
    fig = go.Figure(data=[
        go.Bar(name="Revenue (M)", x=ANIOS, y=REVENUE,
               text=[str(v) for v in REVENUE], textposition="outside"),
        go.Bar(name="Profit (M)", x=ANIOS, y=PROFIT,
               text=[str(v) for v in PROFIT], textposition="outside"),
    ])
    fig.update_layout(barmode="group", title="Plotly Grouped Bar",
                      width=900, height=600, font=dict(size=16))
    fig.write_image(os.path.join(DIR, "plotly_barra.png"))
    filas = [["anio", "revenue", "profit"]] + [
        [a, r, p] for a, r, p in zip(ANIOS, REVENUE, PROFIT)]
    with open(os.path.join(DIR, "plotly_barra.csv"), "w", newline="",
              encoding="utf-8") as f:
        csv.writer(f).writerows(filas)
    print(f"generado: plotly_barra.png  ({len(filas)-1} filas de referencia)")


def main() -> None:
    os.makedirs(DIR, exist_ok=True)
    bar_2series()
    line_3series()
    pie_5()
    bar_apilada()
    scatter_valores()
    seaborn_agrupado()
    plotly_barra()
    print(f"\nDirectorio de pruebas: {DIR}")


if __name__ == "__main__":
    main()
