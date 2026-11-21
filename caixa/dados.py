"""Leitura das bases e calendário de dias úteis."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]


@dataclass
class Base:
    clientes: pd.DataFrame
    fornecedores: pd.DataFrame
    receber: pd.DataFrame
    pagar: pd.DataFrame
    extrato: pd.DataFrame
    saldo_inicial: float
    data_saldo_inicial: pd.Timestamp
    parametros: dict
    feriados: np.ndarray

    @property
    def ultima_data(self) -> pd.Timestamp:
        return self.extrato.data.max()

    def saldo_em(self, data: pd.Timestamp) -> float:
        """Saldo bancário no fim do dia `data`."""
        return self.saldo_inicial + self.extrato.loc[self.extrato.data <= data, "valor"].sum()


def carregar(pasta_dados: Path | None = None, pasta_config: Path | None = None) -> Base:
    d = Path(pasta_dados or RAIZ / "dados")
    c = Path(pasta_config or RAIZ / "config")
    datas = ["emissao", "vencimento", "pagamento"]
    receber = pd.read_csv(d / "receber.csv", parse_dates=datas)
    pagar = pd.read_csv(d / "pagar.csv", parse_dates=datas)
    clientes = pd.read_csv(d / "clientes.csv")
    receber = receber.merge(clientes, on="cliente", how="left", validate="many_to_one")
    if receber.segmento.isna().any():
        raise ValueError("fatura com cliente fora do cadastro")
    saldo = json.loads((d / "saldo_inicial.json").read_text(encoding="utf-8"))
    return Base(
        clientes=clientes,
        fornecedores=pd.read_csv(d / "fornecedores.csv"),
        receber=receber,
        pagar=pagar,
        extrato=pd.read_csv(d / "extrato.csv", parse_dates=["data"], keep_default_na=False,
                            dtype={"documento": str}),
        saldo_inicial=float(saldo["saldo"]),
        data_saldo_inicial=pd.Timestamp(saldo["data"]),
        parametros=json.loads((c / "parametros.json").read_text(encoding="utf-8")),
        feriados=pd.to_datetime(pd.read_csv(c / "feriados.csv").data).values.astype("datetime64[D]"),
    )


class Calendario:
    def __init__(self, feriados: np.ndarray):
        self.hol = feriados

    def rolar(self, datas, sentido: str = "forward") -> np.ndarray:
        return np.busday_offset(np.asarray(datas, dtype="datetime64[D]"), 0, roll=sentido, holidays=self.hol)

    def uteis(self, ini, fim) -> pd.DatetimeIndex:
        d = pd.date_range(ini, fim, freq="D")
        return d[np.is_busday(d.values.astype("datetime64[D]"), holidays=self.hol)]

    def n_util(self, ano: int, mes: int, n: int) -> pd.Timestamp:
        ini = pd.Timestamp(ano, mes, 1)
        return self.uteis(ini, ini + pd.offsets.MonthEnd(0))[n - 1]

    def dia_do_mes(self, ano: int, mes: int, dia: int, ajuste: str) -> pd.Timestamp:
        fim = (pd.Timestamp(ano, mes, 1) + pd.offsets.MonthEnd(0)).day
        data = np.datetime64(pd.Timestamp(ano, mes, min(dia, fim)).date())
        sentido = "forward" if ajuste == "seguinte" else "backward"
        return pd.Timestamp(self.rolar([data], sentido)[0])
