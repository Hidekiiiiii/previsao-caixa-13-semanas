"""Backtest: refaz a previsão em datas passadas (só com o que se sabia em cada data) e compara
com o que o extrato mostra que aconteceu.

Três métodos:
  * Curvas (o modelo): carteira e vendas futuras distribuídas pelas curvas de recebimento.
  * Vencimento (a planilha comum): cliente paga no vencimento; o vencido há até 90 dias entra
    no dia seguinte e o mais antigo é tratado como perda.
    As demais linhas são iguais às do modelo, então a diferença mede só o valor das curvas.
  * Média de 13 semanas (ingênuo): cada semana futura repete a média das últimas 13.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .dados import Base
from .previsao import CATEGORIA_EXTRATO, _semana, prever

METODOS = ("Curvas de recebimento", "Planilha (no vencimento)", "Média de 13 semanas")


def realizado(base: Base, t: pd.Timestamp, H: int) -> pd.DataFrame:
    e = base.extrato[(base.extrato.data > t) & (base.extrato.data <= t + pd.Timedelta(days=7 * H))]
    e = e.assign(semana=_semana(e.data.values, t))
    receb = e[e.categoria == "Recebimento de clientes"].groupby("semana").valor.sum()
    liq = e.groupby("semana").valor.sum()
    return pd.DataFrame({"recebimentos": receb, "liquido": liq}).reindex(range(1, H + 1), fill_value=0.0)


def _previsto_modelo(base: Base, t: pd.Timestamp, metodo: str, H: int) -> pd.DataFrame:
    pv = prever(base, t, metodo=metodo)
    m = pv.movimentos
    receb = m[m.linha.map(CATEGORIA_EXTRATO) == "Recebimento de clientes"].groupby("semana").valor.sum()
    liq = m.groupby("semana").valor.sum()
    return pd.DataFrame({"recebimentos": receb, "liquido": liq}).reindex(range(1, H + 1), fill_value=0.0)


def _previsto_media(base: Base, t: pd.Timestamp, H: int) -> pd.DataFrame:
    e = base.extrato[(base.extrato.data > t - pd.Timedelta(days=91)) & (base.extrato.data <= t)]
    receb = e.loc[e.categoria == "Recebimento de clientes", "valor"].sum() / 13
    liq = e.valor.sum() / 13
    return pd.DataFrame({"recebimentos": receb, "liquido": liq}, index=range(1, H + 1))


def datas_base(base: Base, inicio: str = "2025-01-03", passo_dias: int = 7) -> list[pd.Timestamp]:
    """Sextas-feiras cujo horizonte inteiro (13 semanas) já está no extrato."""
    H = base.parametros["horizonte_semanas"]
    ultima = base.ultima_data - pd.Timedelta(days=7 * H - 2)   # a semana 13 pode terminar no domingo
    return list(pd.date_range(inicio, ultima, freq=f"{passo_dias}D"))


def rodar(base: Base, datas: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """Uma linha por (data-base, método, semana) com previsto e realizado."""
    H = base.parametros["horizonte_semanas"]
    out = []
    for t in datas or datas_base(base):
        real = realizado(base, t, H)
        saldo0 = base.saldo_em(t)
        prev = {
            METODOS[0]: _previsto_modelo(base, t, "curvas", H),
            METODOS[1]: _previsto_modelo(base, t, "vencimento", H),
            METODOS[2]: _previsto_media(base, t, H),
        }
        for metodo, p in prev.items():
            df = pd.DataFrame({
                "data_base": t, "metodo": metodo, "semana": range(1, H + 1),
                "receb_previsto": p.recebimentos.values, "receb_real": real.recebimentos.values,
                "saldo_previsto": saldo0 + p.liquido.cumsum().values,
                "saldo_real": saldo0 + real.liquido.cumsum().values,
            })
            out.append(df)
    return pd.concat(out, ignore_index=True)


def resumo(bt: pd.DataFrame) -> pd.DataFrame:
    """Erro por método: recebimentos (WAPE semanal) e saldo final (erro médio absoluto e viés)."""
    g = bt.assign(erro_receb=(bt.receb_previsto - bt.receb_real).abs(),
                  erro_saldo=bt.saldo_previsto - bt.saldo_real)
    linhas = []
    for metodo, d in g.groupby("metodo", sort=False):
        s4, s13 = d[d.semana == 4], d[d.semana == 13]
        linhas.append({
            "metodo": metodo,
            "wape_recebimentos": d.erro_receb.sum() / d.receb_real.abs().sum(),
            "erro_abs_saldo_s4": s4.erro_saldo.abs().mean(),
            "erro_abs_saldo_s13": s13.erro_saldo.abs().mean(),
            "vies_saldo_s13": s13.erro_saldo.mean(),
            "pior_erro_saldo_s13": s13.erro_saldo.abs().max(),
        })
    return pd.DataFrame(linhas).set_index("metodo").reindex(METODOS)


def erro_por_semana(bt: pd.DataFrame) -> pd.DataFrame:
    e = bt.assign(erro=(bt.saldo_previsto - bt.saldo_real).abs())
    return e.pivot_table(index="semana", columns="metodo", values="erro", aggfunc="mean")[list(METODOS)]
