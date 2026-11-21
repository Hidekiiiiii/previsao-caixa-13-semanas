"""Cenários de atraso de clientes: quanto o caixa aguenta antes de furar o mínimo."""
from __future__ import annotations

import pandas as pd

from .dados import Base
from .previsao import prever


def simular(base: Base, data_base, atraso_extra: dict[str, int]) -> dict:
    pv = prever(base, data_base, atraso_extra=atraso_extra)
    s = pv.saldos_finais()
    return {"atraso_extra": atraso_extra, "saldo_minimo_previsto": float(s.min()), "semana_do_minimo": s.idxmin(),
            "saldo_final_s13": float(s.iloc[-1]), "semanas_abaixo_do_minimo": [a["semana"] for a in pv.alertas()]}


def folga_em_dias(base: Base, data_base, segmento: str, limite: int = 120) -> int | None:
    """Maior atraso adicional (em dias) do segmento que ainda mantém o caixa acima do mínimo
    nas 13 semanas. None = já fura no cenário base; `limite` = aguenta pelo menos isso."""
    def fura(d: int) -> bool:
        return bool(prever(base, data_base, atraso_extra={segmento: d}).alertas())
    if fura(0):
        return None
    if not fura(limite):
        return limite
    ok, ruim = 0, limite                      # busca binária: o saldo só piora com mais atraso
    while ruim - ok > 1:
        meio = (ok + ruim) // 2
        ok, ruim = (meio, ruim) if not fura(meio) else (ok, meio)
    return ok


def tabela_folgas(base: Base, data_base) -> pd.DataFrame:
    segs = sorted(base.clientes.segmento.unique())
    return pd.DataFrame({"segmento": segs, "folga_dias": [folga_em_dias(base, data_base, s) for s in segs]})
