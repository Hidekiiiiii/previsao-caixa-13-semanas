"""Gráficos do relatório (PNG em docs/). Mesma linguagem visual do P4: paleta categórica fixa,
texto em tinta neutra, grade recessiva, sem eixo duplo."""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from .backtest import METODOS, erro_por_semana  # noqa: E402
from .previsao import DMIN, GRADE, Previsao, curvas_por_segmento  # noqa: E402

AZUL, LARANJA, AQUA, AMARELO = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
FUNDO, TINTA, TINTA_2, GRADE_COR = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "figure.facecolor": FUNDO, "axes.facecolor": FUNDO, "savefig.facecolor": FUNDO,
    "font.family": "DejaVu Sans", "font.size": 10, "text.color": TINTA, "axes.labelcolor": TINTA_2,
    "xtick.color": TINTA_2, "ytick.color": TINTA_2, "axes.edgecolor": GRADE_COR,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "axes.axisbelow": True,
    "grid.color": GRADE_COR, "grid.linewidth": 0.8,
})
MI = FuncFormatter(lambda v, _: f"{v / 1e6:.0f}")
FONTE = "Dados fictícios do Grupo Atlas, gerados por script. Elaboração própria."


def _moldura(fig, titulo: str, subtitulo: str) -> None:
    fig.text(0.012, 0.975, titulo, fontsize=14, fontweight="bold", va="top")
    fig.text(0.012, 0.925, subtitulo, fontsize=10, va="top", color=TINTA_2)
    fig.text(0.012, 0.012, FONTE, fontsize=8, color=TINTA_2)


def _br(v: float, casas: int = 1) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def saldo_projetado(pv: Previsao, estresse: Previsao, historico: pd.Series, pasta: Path) -> Path:
    """Saldo semanal: 13 semanas reais antes da data-base + previsão base e cenário de estresse."""
    inicio = pv.inicio_semanas()
    x_prev = [d + pd.Timedelta(days=6) for d in inicio]
    base_s, est_s = pv.saldos_finais().values, estresse.saldos_finais().values
    fig, ax = plt.subplots(figsize=(10, 5.4))
    fig.subplots_adjust(left=0.08, right=0.86, top=0.82, bottom=0.14)
    ax.plot(historico.index, historico.values, color=TINTA_2, lw=2, label="Realizado")
    x0, y0 = [pv.data_base], [pv.saldo_inicial]
    ax.plot(x0 + x_prev, list(y0) + list(base_s), color=AZUL, lw=2, marker="o", ms=5,
            mec=FUNDO, mew=1.5, label="Previsão")
    ax.plot(x0 + x_prev, list(y0) + list(est_s), color=LARANJA, lw=2, marker="o", ms=5,
            mec=FUNDO, mew=1.5, label="Hospitais públicos +30 dias")
    ax.axhline(pv.saldo_minimo, color=TINTA, lw=1, ls=(0, (4, 3)))
    ax.text(historico.index[0], pv.saldo_minimo - 150_000, f"Caixa mínimo: R$ {_br(pv.saldo_minimo / 1e6, 0)} mi",
            fontsize=8.5, color=TINTA_2, va="top")
    ax.axvline(pv.data_base, color=GRADE_COR, lw=1.5)
    ax.text(pv.data_base, ax.get_ylim()[1], "  data-base", fontsize=8.5, color=TINTA_2, va="top")
    for y, nome in ((base_s[-1], "Previsão"), (est_s[-1], "Públicos +30 dias")):
        ax.text(x_prev[-1] + pd.Timedelta(days=4), y, nome, va="center", fontsize=9, color=TINTA_2)
    i = int(np.argmin(est_s))
    ax.annotate(f"menor saldo: R$ {_br(est_s[i] / 1e6)} mi", (x_prev[i], est_s[i]),
                (x_prev[i], est_s[i] - 1_100_000), ha="center", va="top", fontsize=8.5, color=TINTA_2,
                arrowprops={"arrowstyle": "-", "color": TINTA_2, "lw": 0.8})
    ax.yaxis.set_major_formatter(MI)
    ax.set_ylabel("saldo no fim da semana (R$ mi)")
    ax.set_ylim(0, max(base_s.max(), historico.max()) * 1.18)
    ax.legend(loc="lower left", frameon=False, fontsize=8.5, ncol=3)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d/%m"))
    _moldura(fig, "Saldo projetado para as próximas 13 semanas",
             f"Data-base {pv.data_base:%d/%m/%Y}. No cenário de estresse, os hospitais públicos pagam 30 dias mais tarde.")
    arq = pasta / "saldo_projetado.png"
    fig.savefig(arq, dpi=150)
    plt.close(fig)
    return arq


def curvas_recebimento(base, data_base, pasta: Path) -> Path:
    """% do valor recebido x dias após o vencimento, por segmento (Kaplan-Meier)."""
    curvas = curvas_por_segmento(base, pd.Timestamp(data_base), base.parametros["janela_curvas_dias"])
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.subplots_adjust(left=0.08, right=0.97, top=0.82, bottom=0.13)
    cores = [AZUL, LARANJA, AQUA, AMARELO]
    lim = 120
    sel = (GRADE >= DMIN) & (GRADE <= lim)
    ordem = sorted(curvas, key=lambda s: -np.searchsorted(-curvas[s], -0.5))
    for cor, seg in zip(cores, ordem):
        rec = 100 * (1 - curvas[seg][sel])
        mediana = GRADE[np.argmax(1 - curvas[seg] >= 0.5)]
        quando = f"{abs(mediana)} dias {'depois do' if mediana >= 0 else 'antes do'} vencimento"
        ax.plot(GRADE[sel], rec, color=cor, lw=2,
                label=f"{seg}: metade paga até {quando}; {_br(100 * (1 - curvas[seg][-1]))}% recebido no fim")
    ax.axvline(0, color=TINTA_2, lw=1)
    ax.text(1, 3, "vencimento", fontsize=8.5, color=TINTA_2)
    ax.set_xlabel("dias depois do vencimento")
    ax.set_ylabel("% do valor já recebido")
    ax.set_ylim(0, 102)
    ax.set_xlim(DMIN, lim)
    ax.legend(loc="lower right", frameon=False, fontsize=8.5)
    _moldura(fig, "Curvas de recebimento por segmento",
             "Kaplan-Meier com as faturas que venceram nos últimos 240 dias, inclusive as ainda em aberto")
    arq = pasta / "curvas_recebimento.png"
    fig.savefig(arq, dpi=150)
    plt.close(fig)
    return arq


def erro_backtest(bt: pd.DataFrame, pasta: Path) -> Path:
    e = erro_por_semana(bt)
    n = bt.data_base.nunique()
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.subplots_adjust(left=0.08, right=0.8, top=0.82, bottom=0.13)
    for cor, m in zip([AZUL, LARANJA, AQUA], METODOS):
        ax.plot(e.index, e[m].values, color=cor, lw=2, marker="o", ms=5, mec=FUNDO, mew=1.5, label=m)
        ax.text(13.3, e[m].iloc[-1], f"{m}\nR$ {_br(e[m].iloc[-1] / 1e6)} mi", va="center", fontsize=8.5,
                color=TINTA_2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _br(v / 1e6, 0)))
    ax.set_ylabel("erro médio do saldo previsto (R$ mi)")
    ax.set_xlabel("semanas à frente")
    ax.set_xticks(range(1, 14))
    ax.set_ylim(0, e.values.max() * 1.15)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5)
    _moldura(fig, "Quanto cada método errou o saldo, olhando para trás",
             f"Backtest em {n} datas-base semanais de {bt.data_base.min():%d/%m/%Y} a {bt.data_base.max():%d/%m/%Y}; "
             "cada previsão usa só o que se sabia na data")
    arq = pasta / "erro_backtest.png"
    fig.savefig(arq, dpi=150)
    plt.close(fig)
    return arq
