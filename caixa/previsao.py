"""Previsão de caixa de 13 semanas pelo método direto.

Cada linha do fluxo vem de uma fonte que o controller reconhece:
  * Recebimentos da carteira: cada fatura em aberto, distribuída no tempo pela curva de
    recebimento do segmento (Kaplan-Meier, que considera as faturas ainda não pagas).
  * Recebimentos de vendas futuras: venda prevista por dia útil (nível recente x sazonalidade)
    + prazo do segmento + a mesma curva de atraso.
  * Fornecedores: títulos em aberto no vencimento + compras futuras (proporção compras/vendas).
  * Folha, encargos, aluguel, empréstimo, 13º: regras de calendário de config/parametros.json,
    com o último valor pago.
  * Impostos: alíquota efetiva recente x faturamento do mês anterior.
  * Despesas diversas: média por dia útil das últimas 13 semanas.

Nenhuma informação posterior à data-base é usada: é isso que permite o backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .dados import Base, Calendario

DMIN, DMAX = -10, 400          # dias de atraso considerados (negativo = pagou antes do vencimento)
GRADE = np.arange(DMIN, DMAX + 1)

RECEB_CARTEIRA = "Recebimentos – carteira"
RECEB_FUTURO = "Recebimentos – vendas futuras"
FORN_ABERTO = "Fornecedores – títulos em aberto"
FORN_FUTURO = "Fornecedores – compras futuras"
CATEGORIA_EXTRATO = {RECEB_CARTEIRA: "Recebimento de clientes", RECEB_FUTURO: "Recebimento de clientes",
                     FORN_ABERTO: "Fornecedores", FORN_FUTURO: "Fornecedores"}


# --------------------------------------------------------------------------- curvas
def curva_sobrevivencia(dur: np.ndarray, evento: np.ndarray) -> np.ndarray:
    """Kaplan-Meier discreto: S[i] = P(atraso > GRADE[i]).

    `dur` = dias entre o vencimento e o pagamento (evento) ou até a data-base (censurado).
    Sem tratar a censura, as faturas que ainda não foram pagas sumiriam da amostra e a
    curva diria que o cliente paga mais rápido do que paga.
    """
    dur = np.clip(dur, DMIN, DMAX) - DMIN
    n = len(GRADE)
    eventos = np.bincount(dur[evento], minlength=n)[:n]
    saidas = np.bincount(dur, minlength=n)[:n]
    em_risco = saidas[::-1].cumsum()[::-1]
    h = np.divide(eventos, em_risco, out=np.zeros(n), where=em_risco > 0)
    return np.cumprod(1 - h)


def curvas_por_segmento(base: Base, data_base: pd.Timestamp, janela: int) -> dict[str, np.ndarray]:
    r = base.receber
    obs = r[(r.vencimento <= data_base) & (r.vencimento > data_base - pd.Timedelta(days=janela))
            & (r.emissao <= data_base)]
    pago = obs.pagamento.notna() & (obs.pagamento <= data_base)
    fim = obs.pagamento.where(pago, data_base)
    dur = (fim - obs.vencimento).dt.days.to_numpy()
    return {seg: curva_sobrevivencia(dur[(obs.segmento == seg).to_numpy()], pago[obs.segmento == seg].to_numpy())
            for seg in sorted(r.segmento.unique())}


def pmf_atraso(S: np.ndarray) -> np.ndarray:
    """Probabilidade de pagar exatamente com GRADE[i] dias de atraso (o resto = não paga)."""
    return -np.diff(np.concatenate([[1.0], S]))


# --------------------------------------------------------------------------- vendas
def vendas_diarias(base: Base, cal: Calendario, data_base: pd.Timestamp) -> pd.Series:
    r = base.receber[base.receber.emissao <= data_base]
    dias = cal.uteis(r.emissao.min(), data_base)
    return r.groupby("emissao").valor.sum().reindex(dias, fill_value=0.0)


def prever_vendas(base: Base, cal: Calendario, data_base: pd.Timestamp, dias: pd.DatetimeIndex) -> pd.Series:
    """Nível dos últimos N dias úteis, sem sazonalidade, x índice do mês (últimos 12 meses fechados)."""
    hist = vendas_diarias(base, cal, data_base)
    meses = hist.groupby(hist.index.to_period("M")).mean()
    fechados = meses[meses.index < data_base.to_period("M")].iloc[-12:]
    indice = (fechados / fechados.mean()).groupby(fechados.index.month).mean() if len(fechados) == 12 else None
    ind = (lambda m: indice.get(m, 1.0)) if indice is not None else (lambda m: 1.0)
    recente = hist.iloc[-base.parametros["janela_vendas_dias_uteis"]:]
    nivel = recente.mean() / np.mean([ind(m) for m in recente.index.month])
    return pd.Series([nivel * ind(m) for m in dias.month], index=dias)


def pmf_prazo(emissao: pd.Series, vencimento: pd.Series, valor: pd.Series) -> np.ndarray:
    dias = (vencimento - emissao).dt.days.clip(0, 150).to_numpy()
    p = np.bincount(dias, weights=valor.to_numpy(), minlength=151)
    return p / p.sum()


# --------------------------------------------------------------------------- previsão
@dataclass
class Previsao:
    data_base: pd.Timestamp
    saldo_inicial: float
    saldo_minimo: float
    movimentos: pd.DataFrame                 # data, semana, linha, valor
    faturas: pd.DataFrame                    # carteira em aberto com recebimento esperado
    nao_recebido: float                      # carteira que a curva não espera receber no horizonte
    metodo: str = "curvas"
    premissas: dict = field(default_factory=dict)

    def semanal(self) -> pd.DataFrame:
        h = int(self.movimentos.semana.max()) if len(self.movimentos) else 13
        semanas = range(1, h + 1)
        tab = self.movimentos.pivot_table(index="linha", columns="semana", values="valor", aggfunc="sum")
        tab = tab.reindex(columns=semanas, fill_value=0.0).fillna(0.0)
        ordem = [RECEB_CARTEIRA, RECEB_FUTURO, FORN_ABERTO, FORN_FUTURO, "Folha", "Encargos", "13º salário",
                 "Impostos", "Aluguel e serviços", "Empréstimo", "Despesas diversas"]
        tab = tab.reindex([l for l in ordem if l in tab.index] + [l for l in tab.index if l not in ordem])
        liq = tab.sum()
        final = self.saldo_inicial + liq.cumsum()
        inicio = final - liq
        tab.loc["Fluxo líquido"] = liq
        tab = pd.concat([pd.DataFrame([inicio], index=["Saldo inicial"]), tab,
                         pd.DataFrame([final], index=["Saldo final"])])
        tab.columns = [f"S{s}" for s in semanas]
        return tab

    def inicio_semanas(self) -> list[pd.Timestamp]:
        h = int(self.movimentos.semana.max())
        return [self.data_base + pd.Timedelta(days=1 + 7 * i) for i in range(h)]

    def saldos_finais(self) -> pd.Series:
        return self.semanal().loc["Saldo final"]

    def alertas(self) -> list[dict]:
        out = []
        for i, (s, v) in enumerate(self.saldos_finais().items()):
            if v < self.saldo_minimo:
                out.append({"semana": s, "inicio": self.inicio_semanas()[i].date().isoformat(),
                            "saldo_final": round(float(v), 2),
                            "falta_para_minimo": round(float(self.saldo_minimo - v), 2)})
        return out


def _semana(datas: np.ndarray, data_base: pd.Timestamp) -> np.ndarray:
    return (pd.to_datetime(datas) - (data_base + pd.Timedelta(days=1))).days.to_numpy() // 7 + 1


def prever(base: Base, data_base, metodo: str = "curvas", atraso_extra: dict[str, int] | None = None) -> Previsao:
    """Previsão a partir de `data_base`. metodo = 'curvas' (o modelo) ou 'vencimento' (planilha:
    todo cliente paga no vencimento e o que está vencido entra no dia seguinte)."""
    if metodo not in ("curvas", "vencimento"):
        raise ValueError("metodo deve ser 'curvas' ou 'vencimento'")
    t = pd.Timestamp(data_base)
    if t > base.ultima_data:
        raise ValueError(f"data-base depois do último dia do extrato ({base.ultima_data.date()})")
    p = base.parametros
    H = p["horizonte_semanas"]
    fim = t + pd.Timedelta(days=7 * H)
    cal = Calendario(base.feriados)
    extra = atraso_extra or {}
    linhas: list[pd.DataFrame] = []

    def junta(datas, valores, linha):
        datas = cal.rolar(datas)
        df = pd.DataFrame({"data": pd.to_datetime(datas), "valor": valores, "linha": linha})
        df = df[(df.data > t) & (df.data <= fim) & (df.valor != 0)]
        linhas.append(df.groupby(["data", "linha"], as_index=False).valor.sum())

    curvas = curvas_por_segmento(base, t, p["janela_curvas_dias"])

    # 1) carteira em aberto
    r = base.receber
    aberto = r[(r.emissao <= t) & (r.pagamento.isna() | (r.pagamento > t))].copy()
    esperado = np.zeros(len(aberto))
    for seg, grupo in aberto.groupby("segmento"):
        pos = aberto.index.get_indexer(grupo.index)
        venc = grupo.vencimento.values.astype("datetime64[D]")
        if metodo == "vencimento":
            # Planilha: o que está em dia entra no vencimento; o vencido há até 90 dias entra no
            # dia seguinte; o vencido há mais tempo é tratado como perda (provisionado).
            em_dia = ((t - grupo.vencimento).dt.days <= 90).to_numpy()
            datas = np.maximum(venc, np.datetime64((t + pd.Timedelta(days=1)).date()))
            valores = grupo.valor.values * em_dia
            junta(datas, valores, RECEB_CARTEIRA)
            esperado[pos] = valores * (datas <= np.datetime64(fim.date()))
            continue
        S = curvas[seg]
        k = (t - grupo.vencimento).dt.days.to_numpy()                      # dias já decorridos
        ki = np.clip(k - DMIN, -1, len(GRADE) - 1)
        S_k = np.where(ki < 0, 1.0, S[np.maximum(ki, 0)])
        pmf = pmf_atraso(S)[None, :] * (GRADE[None, :] > k[:, None])       # só atrasos ainda possíveis
        prob = np.divide(pmf, S_k[:, None], out=np.zeros_like(pmf), where=S_k[:, None] > 1e-9)
        d = extra.get(seg, 0)
        datas = venc[:, None] + (GRADE + d).astype("timedelta64[D]")[None, :]
        no_horizonte = (datas > np.datetime64(t.date())) & (datas <= np.datetime64(fim.date()))
        valores = grupo.valor.values[:, None] * prob
        junta(datas.ravel(), valores.ravel(), RECEB_CARTEIRA)
        esperado[pos] = (valores * no_horizonte).sum(axis=1)
    aberto["recebimento_esperado_no_horizonte"] = esperado.round(2)
    aberto["dias_vencido"] = (t - aberto.vencimento).dt.days

    # 2) vendas futuras
    dias_fut = cal.uteis(t + pd.Timedelta(days=1), fim)
    vendas = prever_vendas(base, cal, t, dias_fut)
    recentes = r[(r.emissao > t - pd.Timedelta(days=90)) & (r.emissao <= t)]
    mix = recentes.groupby("segmento").valor.sum() / recentes.valor.sum()
    for seg, share in mix.items():
        rs = recentes[recentes.segmento == seg]
        prazo = pmf_prazo(rs.emissao, rs.vencimento, rs.valor)
        if metodo == "vencimento":
            off, pm = np.arange(len(prazo)), prazo
        else:
            atraso = pmf_atraso(curvas[seg])
            pm = np.convolve(prazo, atraso)
            off = np.arange(len(pm)) + DMIN + extra.get(seg, 0)
        datas = dias_fut.values.astype("datetime64[D]")[:, None] + off.astype("timedelta64[D]")[None, :]
        valores = (vendas.values * share)[:, None] * pm[None, :]
        junta(datas.ravel(), valores.ravel(), RECEB_FUTURO)

    # 3) fornecedores em aberto e compras futuras
    pg = base.pagar
    ab = pg[(pg.emissao <= t) & (pg.pagamento.isna() | (pg.pagamento > t))]
    datas = np.maximum(ab.vencimento.values.astype("datetime64[D]"), np.datetime64((t + pd.Timedelta(days=1)).date()))
    junta(datas, -ab.valor.values, FORN_ABERTO)
    comp = pg[(pg.emissao > t - pd.Timedelta(days=90)) & (pg.emissao <= t)]
    razao = comp.valor.sum() / recentes.valor.sum()
    comp180 = pg[(pg.emissao > t - pd.Timedelta(days=180)) & (pg.emissao <= t)]
    prazo_f = pmf_prazo(comp180.emissao, comp180.vencimento, comp180.valor)
    datas = dias_fut.values.astype("datetime64[D]")[:, None] + np.arange(len(prazo_f)).astype("timedelta64[D]")[None, :]
    junta(datas.ravel(), (-(vendas.values * razao)[:, None] * prazo_f[None, :]).ravel(), FORN_FUTURO)

    # 4) recorrentes por regra de calendário
    ext = base.extrato[base.extrato.data <= t]
    ultimo = ext.groupby("categoria").valor.last()
    meses = pd.period_range(t, fim, freq="M")
    for regra in p["recorrentes"]:
        ref = regra.get("base", regra["categoria"])
        if ref not in ultimo:
            continue
        valor = ultimo[ref] * regra.get("fator", 1.0)
        for m in meses:
            if "meses" in regra and m.month not in regra["meses"]:
                continue
            if regra["regra"] == "dia_util":
                d = cal.n_util(m.year, m.month, regra["dia"])
            else:
                d = cal.dia_do_mes(m.year, m.month, regra["dia"], regra.get("ajuste", "seguinte"))
            junta(np.array([np.datetime64(d.date())]), [valor], regra["categoria"])

    # 5) impostos sobre o faturamento do mês anterior
    imp = p["impostos"]
    hist = vendas_diarias(base, cal, t)
    vendas_mes = pd.concat([hist, vendas]).groupby(lambda d: d.to_period("M")).sum()
    pag_imp = ext[ext.categoria == imp["categoria"]]
    aliq = [-v / vendas_mes[d.to_period("M") - 1] for d, v in zip(pag_imp.data, pag_imp.valor)
            if d.to_period("M") - 1 in vendas_mes.index][-3:]
    aliquota = float(np.median(aliq)) if aliq else 0.0
    for m in meses:
        d = cal.dia_do_mes(m.year, m.month, imp["dia"], imp["ajuste"])
        if (m - 1) in vendas_mes.index:
            junta(np.array([np.datetime64(d.date())]), [-aliquota * vendas_mes[m - 1]], imp["categoria"])

    # 6) despesas diversas
    div = ext[(ext.categoria == "Despesas diversas") & (ext.data > t - pd.Timedelta(days=91))]
    media = div.valor.sum() / len(cal.uteis(t - pd.Timedelta(days=90), t))
    junta(dias_fut.values, np.full(len(dias_fut), media), "Despesas diversas")

    mov = pd.concat(linhas, ignore_index=True)
    mov["semana"] = _semana(mov.data.values, t)
    mov = mov.sort_values(["data", "linha"]).reset_index(drop=True)
    return Previsao(
        data_base=t, saldo_inicial=base.saldo_em(t), saldo_minimo=float(p["saldo_minimo"]),
        movimentos=mov, faturas=aberto, metodo=metodo,
        nao_recebido=float(aberto.valor.sum() - esperado.sum()),
        premissas={"aliquota_impostos": aliquota, "compras_sobre_vendas": float(razao),
                   "venda_media_dia_util": float(vendas.mean()), "mix_segmentos": mix.round(4).to_dict(),
                   "atraso_extra": extra},
    )
