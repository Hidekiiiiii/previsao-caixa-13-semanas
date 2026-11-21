"""API da previsão de caixa (FastAPI).

    uvicorn caixa.api:app --reload
    http://127.0.0.1:8000/docs  -> documentação interativa

A base é lida uma vez, na primeira chamada. Todas as respostas usam valores em reais.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import backtest
from .cenarios import folga_em_dias
from .dados import Base, carregar
from .previsao import Previsao, prever

app = FastAPI(
    title="Previsão de caixa de 13 semanas",
    description="Grupo Atlas (dados fictícios). Método direto com curvas de recebimento, cenários de atraso "
                "e backtest.",
    version="1.0.0",
)


# ------------------------------------------------------------------ modelos de resposta
class Semana(BaseModel):
    semana: int
    inicio: date
    fim: date
    saldo_inicial: float
    linhas: dict[str, float]
    fluxo_liquido: float
    saldo_final: float
    abaixo_do_minimo: bool


class Alerta(BaseModel):
    semana: str
    inicio: date
    saldo_final: float
    falta_para_minimo: float


class RespostaPrevisao(BaseModel):
    data_base: date
    metodo: str
    saldo_atual: float
    saldo_minimo: float
    menor_saldo: float
    semana_do_menor_saldo: int
    carteira_nao_recebida_no_horizonte: float
    atraso_extra: dict[str, int]
    alertas: list[Alerta]
    semanas: list[Semana]


class PedidoCenario(BaseModel):
    data_base: date | None = Field(None, description="Padrão: último dia do extrato.")
    atraso_extra: dict[str, int] = Field(
        ..., description="Dias de atraso a mais por segmento.",
        examples=[{"Hospital público": 30}])


class RespostaCenario(BaseModel):
    base: RespostaPrevisao
    cenario: RespostaPrevisao
    diferenca_menor_saldo: float
    diferenca_saldo_final: float


class Folga(BaseModel):
    segmento: str
    folga_dias: int | None = Field(description="Atraso adicional tolerado antes de furar o mínimo. "
                                               "null = já fura no cenário base; 120 = pelo menos 120.")


class Fatura(BaseModel):
    fatura: str
    cliente: str
    segmento: str
    vencimento: date
    dias_vencido: int
    valor: float
    recebimento_esperado_no_horizonte: float


class ResumoBacktest(BaseModel):
    metodo: str
    wape_recebimentos: float
    erro_abs_saldo_s4: float
    erro_abs_saldo_s13: float
    vies_saldo_s13: float
    pior_erro_saldo_s13: float


# ------------------------------------------------------------------ apoio
@lru_cache(maxsize=1)
def _base() -> Base:
    return carregar()


def _segmentos() -> list[str]:
    return sorted(_base().clientes.segmento.unique())


def _data(data_base: date | None) -> pd.Timestamp:
    b = _base()
    if data_base is None:
        return b.ultima_data
    t = pd.Timestamp(data_base)
    inicio = b.receber.emissao.min() + pd.Timedelta(days=365)
    if t > b.ultima_data or t < inicio:
        raise HTTPException(422, f"data_base deve estar entre {inicio.date()} e {b.ultima_data.date()} "
                                 "(é preciso 1 ano de histórico para as curvas e a sazonalidade)")
    return t


def _valida_segmentos(atraso: dict[str, int]) -> None:
    errados = sorted(set(atraso) - set(_segmentos()))
    if errados:
        raise HTTPException(422, f"segmento desconhecido: {errados}. Use um de {_segmentos()}")
    if any(not 0 <= d <= 180 for d in atraso.values()):
        raise HTTPException(422, "atraso_extra deve ficar entre 0 e 180 dias")


def _resposta(pv: Previsao) -> RespostaPrevisao:
    tab = pv.semanal()
    inicios = pv.inicio_semanas()
    semanas = []
    for i, col in enumerate(tab.columns):
        c = tab[col]
        linhas = {k: round(float(v), 2) for k, v in c.items()
                  if k not in ("Saldo inicial", "Fluxo líquido", "Saldo final")}
        semanas.append(Semana(
            semana=i + 1, inicio=inicios[i].date(), fim=(inicios[i] + pd.Timedelta(days=6)).date(),
            saldo_inicial=round(float(c["Saldo inicial"]), 2), linhas=linhas,
            fluxo_liquido=round(float(c["Fluxo líquido"]), 2), saldo_final=round(float(c["Saldo final"]), 2),
            abaixo_do_minimo=bool(c["Saldo final"] < pv.saldo_minimo)))
    finais = tab.loc["Saldo final"]
    return RespostaPrevisao(
        data_base=pv.data_base.date(), metodo=pv.metodo, saldo_atual=round(pv.saldo_inicial, 2),
        saldo_minimo=pv.saldo_minimo, menor_saldo=round(float(finais.min()), 2),
        semana_do_menor_saldo=int(finais.values.argmin()) + 1,
        carteira_nao_recebida_no_horizonte=round(pv.nao_recebido, 2),
        atraso_extra=pv.premissas["atraso_extra"], alertas=[Alerta(**a) for a in pv.alertas()],
        semanas=semanas)


# ------------------------------------------------------------------ rotas
@app.get("/", summary="O que a API oferece")
def raiz() -> dict:
    b = _base()
    return {"empresa": "Grupo Atlas (dados fictícios)", "ultima_data_do_extrato": b.ultima_data.date(),
            "segmentos": _segmentos(), "documentacao": "/docs",
            "rotas": ["/previsao", "/cenario", "/folga", "/carteira", "/backtest"]}


@app.get("/previsao", response_model=RespostaPrevisao, summary="Fluxo de caixa das próximas 13 semanas")
def rota_previsao(data_base: date | None = Query(None, description="AAAA-MM-DD; padrão: último dia do extrato"),
                  metodo: Literal["curvas", "vencimento"] = "curvas") -> RespostaPrevisao:
    return _resposta(prever(_base(), _data(data_base), metodo=metodo))


@app.post("/cenario", response_model=RespostaCenario, summary="Simula atraso adicional de clientes")
def rota_cenario(pedido: PedidoCenario) -> RespostaCenario:
    _valida_segmentos(pedido.atraso_extra)
    t = _data(pedido.data_base)
    base = _resposta(prever(_base(), t))
    cen = _resposta(prever(_base(), t, atraso_extra=pedido.atraso_extra))
    return RespostaCenario(base=base, cenario=cen,
                           diferenca_menor_saldo=round(cen.menor_saldo - base.menor_saldo, 2),
                           diferenca_saldo_final=round(cen.semanas[-1].saldo_final - base.semanas[-1].saldo_final, 2))


@app.get("/folga", response_model=list[Folga], summary="Quantos dias de atraso cada segmento pode ter")
def rota_folga(data_base: date | None = None) -> list[Folga]:
    t = _data(data_base)
    return [Folga(segmento=s, folga_dias=folga_em_dias(_base(), t, s)) for s in _segmentos()]


@app.get("/carteira", response_model=list[Fatura], summary="Faturas em aberto e quanto deve entrar")
def rota_carteira(data_base: date | None = None, segmento: str | None = None,
                  vencidas_ha_mais_de: int = Query(0, ge=0, description="dias"),
                  limite: int = Query(100, ge=1, le=2000)) -> list[Fatura]:
    if segmento is not None:
        _valida_segmentos({segmento: 0})
    f = prever(_base(), _data(data_base)).faturas
    if segmento:
        f = f[f.segmento == segmento]
    f = f[f.dias_vencido > vencidas_ha_mais_de] if vencidas_ha_mais_de else f
    f = f.sort_values("valor", ascending=False).head(limite)
    return [Fatura(fatura=r.fatura, cliente=r.cliente, segmento=r.segmento, vencimento=r.vencimento.date(),
                   dias_vencido=int(r.dias_vencido), valor=round(float(r.valor), 2),
                   recebimento_esperado_no_horizonte=round(float(r.recebimento_esperado_no_horizonte), 2))
            for r in f.itertuples()]


@lru_cache(maxsize=1)
def _resumo_backtest() -> pd.DataFrame:
    return backtest.resumo(backtest.rodar(_base()))


@app.get("/backtest", response_model=list[ResumoBacktest], summary="Erro de cada método no passado")
def rota_backtest() -> list[ResumoBacktest]:
    r = _resumo_backtest().reset_index()
    return [ResumoBacktest(**{k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})
            for row in r.to_dict("records")]
