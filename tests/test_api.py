"""Testes da API com o cliente de testes do FastAPI (sem subir servidor)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from caixa.api import app
from caixa.dados import carregar
from caixa.previsao import prever

cliente = TestClient(app)


@pytest.fixture(scope="module")
def previsao_direta():
    return prever(carregar(), "2025-10-31")


def test_previsao_bate_com_o_motor(previsao_direta):
    r = cliente.get("/previsao", params={"data_base": "2025-10-31"})
    assert r.status_code == 200
    j = r.json()
    assert len(j["semanas"]) == 13
    assert j["saldo_atual"] == pytest.approx(previsao_direta.saldo_inicial, abs=0.01)
    assert j["semanas"][-1]["saldo_final"] == pytest.approx(previsao_direta.saldos_finais().iloc[-1], abs=0.01)
    s1 = j["semanas"][0]
    assert s1["inicio"] == "2025-11-01" and s1["fim"] == "2025-11-07"
    assert s1["saldo_inicial"] + sum(s1["linhas"].values()) == pytest.approx(s1["saldo_final"], abs=0.1)


def test_data_base_padrao_e_o_ultimo_dia_do_extrato():
    assert cliente.get("/previsao").json()["data_base"] == "2025-10-31"


def test_cenario_piora_e_gera_alerta():
    r = cliente.post("/cenario", json={"atraso_extra": {"Hospital público": 30}})
    assert r.status_code == 200
    j = r.json()
    assert j["diferenca_menor_saldo"] < 0
    assert not j["base"]["alertas"] and j["cenario"]["alertas"]


@pytest.mark.parametrize("chamada", [
    lambda: cliente.get("/previsao", params={"data_base": "2026-03-01"}),        # depois do extrato
    lambda: cliente.get("/previsao", params={"data_base": "2024-02-01"}),        # sem 1 ano de histórico
    lambda: cliente.get("/previsao", params={"metodo": "chute"}),
    lambda: cliente.post("/cenario", json={"atraso_extra": {"Farmácia": 10}}),
    lambda: cliente.post("/cenario", json={"atraso_extra": {"Clínica": -5}}),
    lambda: cliente.get("/carteira", params={"segmento": "Farmácia"}),
])
def test_entradas_invalidas_viram_422(chamada):
    assert chamada().status_code == 422


def test_carteira_filtra_e_ordena():
    j = cliente.get("/carteira", params={"segmento": "Clínica", "vencidas_ha_mais_de": 90, "limite": 5}).json()
    assert 0 < len(j) <= 5
    assert all(f["segmento"] == "Clínica" and f["dias_vencido"] > 90 for f in j)
    assert [f["valor"] for f in j] == sorted((f["valor"] for f in j), reverse=True)


def test_folga_lista_todos_os_segmentos():
    j = cliente.get("/folga").json()
    assert {f["segmento"] for f in j} == {"Clínica", "Distribuidor", "Hospital privado", "Hospital público"}


def test_pagina_e_info():
    r = cliente.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Grupo Atlas" in r.text and "atlas@caixa" in r.text          # versão principal (terminal)
    c = cliente.get("/classica")
    assert c.status_code == 200 and "Previsão de caixa · Grupo Atlas" in c.text
    j = cliente.get("/info").json()
    assert j["ultima_data_do_extrato"] == "2025-10-31" and j["data_base_minima"] < "2025-01-01"
    assert cliente.get("/web/fontes/inter-latin-wght.woff2").status_code == 200
