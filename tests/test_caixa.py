"""Testes do motor de previsão, do backtest e dos cenários."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from caixa import backtest
from caixa.cenarios import folga_em_dias
from caixa.dados import carregar
from caixa.previsao import (CATEGORIA_EXTRATO, DMIN, GRADE, RECEB_CARTEIRA, curva_sobrevivencia, pmf_atraso,
                            prever)

DATA_BASE = pd.Timestamp("2025-10-31")


@pytest.fixture(scope="module")
def base():
    return carregar()


@pytest.fixture(scope="module")
def pv(base):
    return prever(base, DATA_BASE)


# ----------------------------------------------------------------- curva de recebimento
def test_kaplan_meier_sem_censura_e_a_proporcao_simples():
    dur = np.array([0, 0, 5, 10])
    S = curva_sobrevivencia(dur, np.ones(4, bool))
    em = lambda d: S[d - DMIN]  # noqa: E731
    assert em(-1) == 1 and em(0) == pytest.approx(0.5) and em(5) == pytest.approx(0.25) and em(10) == 0


def test_kaplan_meier_considera_faturas_em_aberto():
    # 2 pagas no dia 0; 2 ainda abertas há 20 dias. Ignorar as abertas diria "100% paga no dia 0".
    dur = np.array([0, 0, 20, 20])
    S = curva_sobrevivencia(dur, np.array([True, True, False, False]))
    assert S[0 - DMIN] == pytest.approx(0.5)
    assert S[20 - DMIN] == pytest.approx(0.5)   # sem evento depois: a curva fica parada


def test_pmf_soma_no_maximo_um_e_curva_nao_cresce():
    rng = np.random.default_rng(1)
    dur = rng.integers(-5, 90, 500)
    S = curva_sobrevivencia(dur, rng.random(500) < 0.9)
    assert np.all(np.diff(S) <= 1e-12)
    assert pmf_atraso(S).sum() == pytest.approx(1 - S[-1])
    assert len(S) == len(GRADE)


# ----------------------------------------------------------------- previsão
def test_saldo_final_fecha_com_os_movimentos(pv):
    tab = pv.semanal()
    assert tab.loc["Saldo inicial", "S1"] == pytest.approx(pv.saldo_inicial)
    assert tab.loc["Saldo final", "S13"] == pytest.approx(pv.saldo_inicial + pv.movimentos.valor.sum())
    assert pv.movimentos.semana.between(1, 13).all()
    assert (pv.movimentos.data > DATA_BASE).all()


def test_saldo_inicial_e_o_do_extrato(base, pv):
    assert pv.saldo_inicial == pytest.approx(base.saldo_inicial + base.extrato.valor.sum())


def test_carteira_conserva_o_valor(pv):
    # Tudo que está em aberto ou entra no horizonte ou fica como "não recebido".
    entra = pv.movimentos.loc[pv.movimentos.linha == RECEB_CARTEIRA, "valor"].sum()
    assert entra + pv.nao_recebido == pytest.approx(pv.faturas.valor.sum(), rel=1e-9)
    assert pv.faturas.recebimento_esperado_no_horizonte.sum() == pytest.approx(entra, rel=1e-6)


def test_nao_usa_informacao_posterior_a_data_base(base):
    t = pd.Timestamp("2025-05-30")
    antes = prever(base, t).movimentos
    # Apaga tudo o que aconteceu depois de t: a previsão tem de ser idêntica.
    b2 = copy.copy(base)
    b2.receber = base.receber[base.receber.emissao <= t].copy()
    b2.receber.loc[b2.receber.pagamento > t, "pagamento"] = pd.NaT
    b2.pagar = base.pagar[base.pagar.emissao <= t].copy()
    b2.pagar.loc[b2.pagar.pagamento > t, "pagamento"] = pd.NaT
    b2.extrato = base.extrato[base.extrato.data <= t]
    depois = prever(b2, t).movimentos
    pd.testing.assert_frame_equal(antes, depois)


def test_calendario_das_saidas_fixas(pv):
    m = pv.movimentos
    datas = lambda l: sorted(m.loc[m.linha == l, "data"].dt.strftime("%Y-%m-%d"))  # noqa: E731
    assert datas("Folha") == ["2025-11-07", "2025-12-05", "2026-01-08"]       # 5º dia útil (feriados contam)
    assert datas("13º salário") == ["2025-11-28", "2025-12-19"]               # 30/11 e 20/12 caem no fim de semana
    assert datas("Empréstimo") == ["2025-12-15"]
    assert datas("Impostos") == ["2025-11-25", "2025-12-26", "2026-01-26"]


def test_metodo_planilha_recebe_no_vencimento(base):
    pv = prever(base, DATA_BASE, metodo="vencimento")
    f = pv.faturas
    em_dia = f[(f.vencimento > DATA_BASE) & (f.vencimento <= DATA_BASE + pd.Timedelta(days=60))]
    vencido_recente = f[f.dias_vencido.between(1, 90)]
    m = pv.movimentos[pv.movimentos.linha == RECEB_CARTEIRA]
    assert m[m.data == "2025-11-03"].valor.sum() >= vencido_recente.valor.sum() - 1e-6   # 1º dia útil
    assert f.recebimento_esperado_no_horizonte.sum() < f.valor.sum()                   # vencido > 90 d = perda
    assert em_dia.valor.sum() > 0


def test_data_base_depois_do_extrato_da_erro(base):
    with pytest.raises(ValueError):
        prever(base, "2026-01-30")


# ----------------------------------------------------------------- cenários
def test_mais_atraso_nunca_melhora_o_caixa(base):
    mins = [prever(base, DATA_BASE, atraso_extra={"Hospital público": d}).saldos_finais().min() for d in (0, 10, 30)]
    assert mins[0] >= mins[1] >= mins[2]


def test_folga_e_o_limite_exato(base):
    f = folga_em_dias(base, DATA_BASE, "Hospital público")
    assert f is not None and 0 < f < 120
    assert not prever(base, DATA_BASE, atraso_extra={"Hospital público": f}).alertas()
    assert prever(base, DATA_BASE, atraso_extra={"Hospital público": f + 1}).alertas()


# ----------------------------------------------------------------- backtest
def test_realizado_bate_com_o_extrato(base):
    t = pd.Timestamp("2025-03-07")
    r = backtest.realizado(base, t, 13)
    e = base.extrato[(base.extrato.data > t) & (base.extrato.data <= t + pd.Timedelta(days=91))]
    assert r.liquido.sum() == pytest.approx(e.valor.sum())
    assert r.recebimentos.sum() == pytest.approx(e[e.categoria == "Recebimento de clientes"].valor.sum())


def test_datas_do_backtest_cabem_no_extrato(base):
    ds = backtest.datas_base(base)
    assert ds[-1] + pd.Timedelta(days=91) <= base.ultima_data + pd.Timedelta(days=2)
    assert all(d.dayofweek == 4 for d in ds)


def test_categorias_do_modelo_existem_no_extrato(base, pv):
    cats = set(base.extrato.categoria)
    linhas = set(pv.movimentos.linha.map(lambda l: CATEGORIA_EXTRATO.get(l, l)))
    assert linhas <= cats
