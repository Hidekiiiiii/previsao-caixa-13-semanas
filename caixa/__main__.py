"""python -m caixa [--data-base AAAA-MM-DD] [--sem-backtest]

Gera saida/previsao_<data>.xlsx, saida/backtest.csv e os gráficos de docs/."""
from __future__ import annotations

import argparse

import pandas as pd

from . import backtest, graficos
from .cenarios import tabela_folgas
from .dados import RAIZ, carregar
from .previsao import prever
from .relatorio import gravar_excel


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Previsão de caixa de 13 semanas (Grupo Atlas, dados fictícios)")
    ap.add_argument("--data-base", help="padrão: último dia do extrato")
    ap.add_argument("--sem-backtest", action="store_true", help="pula o backtest (mais rápido)")
    a = ap.parse_args(argv)

    base = carregar()
    t = pd.Timestamp(a.data_base) if a.data_base else base.ultima_data
    pv = prever(base, t)
    estresse = prever(base, t, atraso_extra={"Hospital público": 30})
    folgas = tabela_folgas(base, t)

    saida, docs = RAIZ / "saida", RAIZ / "docs"
    saida.mkdir(exist_ok=True)
    docs.mkdir(exist_ok=True)
    if a.sem_backtest:
        resumo = pd.DataFrame()
    else:
        bt = backtest.rodar(base)
        bt.to_csv(saida / "backtest.csv", index=False, date_format="%Y-%m-%d")
        resumo = backtest.resumo(bt)
        graficos.erro_backtest(bt, docs)

    hist = (base.saldo_inicial + base.extrato.set_index("data").valor.cumsum()).groupby(level=0).last()
    hist = hist[(hist.index > t - pd.Timedelta(days=91)) & (hist.index <= t)]
    hist = hist.resample("W-FRI").last().dropna()
    graficos.saldo_projetado(pv, estresse, hist, docs)
    graficos.curvas_recebimento(base, t, docs)
    arq = gravar_excel(pv, folgas, resumo, saida / f"previsao_{t:%Y-%m-%d}.xlsx")

    s = pv.saldos_finais()
    print(f"Data-base {t:%d/%m/%Y} · saldo R$ {pv.saldo_inicial:,.0f}")
    print(f"Menor saldo previsto: R$ {s.min():,.0f} na {s.idxmin()} · semana 13: R$ {s.iloc[-1]:,.0f}")
    print(f"Semanas abaixo do mínimo: {[x['semana'] for x in pv.alertas()] or 'nenhuma'}")
    print("Folga de atraso por segmento (dias):", dict(zip(folgas.segmento, folgas.folga_dias)))
    if not resumo.empty:
        print(resumo.round(3).to_string())
    print(f"Planilha: {arq}")


if __name__ == "__main__":
    main()
