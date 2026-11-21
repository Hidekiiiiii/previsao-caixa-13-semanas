"""Planilha de saída para o controller (xlsxwriter)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .previsao import Previsao


def gravar_excel(pv: Previsao, folgas: pd.DataFrame, resumo_bt: pd.DataFrame, arq: Path) -> Path:
    tab = pv.semanal()
    cab = [f"S{i + 1}\n{d:%d/%m}" for i, d in enumerate(pv.inicio_semanas())]
    with pd.ExcelWriter(arq, engine="xlsxwriter") as xw:
        wb = xw.book
        moeda = wb.add_format({"num_format": "#,##0;[Red]-#,##0", "font_size": 10})
        negrito = wb.add_format({"num_format": "#,##0;[Red]-#,##0", "bold": True, "top": 1, "font_size": 10})
        alerta = wb.add_format({"num_format": "#,##0;[Red]-#,##0", "bold": True, "bg_color": "#fde2d6",
                                "top": 1, "font_size": 10})
        cabec = wb.add_format({"bold": True, "text_wrap": True, "align": "center", "bottom": 1, "font_size": 10})
        titulo = wb.add_format({"bold": True, "font_size": 14})
        nota = wb.add_format({"italic": True, "font_color": "#52514e", "font_size": 9})
        pct = wb.add_format({"num_format": "0.0%", "font_size": 10})

        ws = wb.add_worksheet("Fluxo 13 semanas")
        ws.write(0, 0, f"Grupo Atlas · previsão de caixa · data-base {pv.data_base:%d/%m/%Y}", titulo)
        ws.write(1, 0, f"Valores em R$. Caixa mínimo: R$ {pv.saldo_minimo:,.0f}. Semanas começam no dia "
                       f"seguinte à data-base. Dados fictícios.".replace(",", "."), nota)
        ws.write(3, 0, "Linha", cabec)
        for j, c in enumerate(cab):
            ws.write(3, j + 1, c, cabec)
        for i, (linha, valores) in enumerate(tab.iterrows()):
            r = 4 + i
            fmt = negrito if linha in ("Saldo inicial", "Fluxo líquido", "Saldo final") else moeda
            ws.write(r, 0, linha, fmt if fmt is negrito else None)
            for j, v in enumerate(valores):
                f = alerta if linha == "Saldo final" and v < pv.saldo_minimo else fmt
                ws.write_number(r, j + 1, round(float(v), 2), f)
        ws.set_column(0, 0, 34)
        ws.set_column(1, len(cab), 12)
        ws.set_row(3, 30)
        ws.freeze_panes(4, 1)

        ws2 = wb.add_worksheet("Alertas e cenários")
        ws2.write(0, 0, "Semanas abaixo do caixa mínimo (cenário base)", titulo)
        al = pd.DataFrame(pv.alertas())
        if al.empty:
            ws2.write(1, 0, "Nenhuma no cenário base.")
        else:
            al.to_excel(xw, sheet_name="Alertas e cenários", startrow=1, index=False)
        r0 = 4 + len(al)
        ws2.write(r0, 0, "Folga: atraso adicional que cada segmento pode ter antes de o caixa furar o mínimo", titulo)
        folgas.rename(columns={"folga_dias": "dias de atraso adicional tolerados"}).to_excel(
            xw, sheet_name="Alertas e cenários", startrow=r0 + 1, index=False)
        ws2.write(r0 + 3 + len(folgas), 0, "120 = aguenta pelo menos 120 dias (limite da busca).", nota)
        ws2.set_column(0, 0, 26)
        ws2.set_column(1, 4, 22)

        cart = pv.faturas[["fatura", "cliente", "segmento", "emissao", "vencimento", "dias_vencido", "valor",
                           "recebimento_esperado_no_horizonte"]].sort_values("dias_vencido", ascending=False)
        cart.to_excel(xw, sheet_name="Carteira em aberto", index=False)
        ws3 = xw.sheets["Carteira em aberto"]
        ws3.set_column(0, 2, 16)
        ws3.set_column(3, 4, 12, wb.add_format({"num_format": "dd/mm/yyyy"}))
        ws3.set_column(5, 5, 12)
        ws3.set_column(6, 7, 18, moeda)
        ws3.autofilter(0, 0, len(cart), 7)
        ws3.freeze_panes(1, 0)

        bt = resumo_bt.reset_index()
        bt.to_excel(xw, sheet_name="Backtest", index=False)
        ws4 = xw.sheets["Backtest"]
        ws4.set_column(0, 0, 28)
        ws4.set_column(1, 1, 18, pct)
        ws4.set_column(2, 5, 20, moeda)

        prem = pd.DataFrame([
            ("Venda média por dia útil prevista", pv.premissas["venda_media_dia_util"]),
            ("Compras / vendas (90 dias)", pv.premissas["compras_sobre_vendas"]),
            ("Alíquota efetiva de impostos", pv.premissas["aliquota_impostos"]),
            ("Carteira que não deve entrar em 13 semanas", pv.nao_recebido),
        ] + [(f"Mix de vendas: {k}", v) for k, v in pv.premissas["mix_segmentos"].items()],
            columns=["premissa", "valor"])
        prem.to_excel(xw, sheet_name="Premissas", index=False)
        xw.sheets["Premissas"].set_column(0, 0, 44)
        xw.sheets["Premissas"].set_column(1, 1, 18)
    return arq
