"""Gera a base fictícia do Grupo Atlas para a previsão de caixa (semente fixa).

Saídas em dados/:
  clientes.csv, fornecedores.csv  cadastros com prazo de pagamento
  receber.csv                     faturas de venda (pagamento vazio = em aberto na data de corte)
  pagar.csv                       faturas de fornecedores (idem)
  extrato.csv                     todos os movimentos de caixa até a data de corte
  saldo_inicial.json              saldo bancário em 01/01/2024

Comportamentos embutidos de propósito (o modelo precisa descobri-los sozinho):
  * hospitais públicos pagam com ~35 dias de atraso e, a partir de jun/25, ~20 dias a mais;
  * clínicas têm 3% de faturas que nunca são pagas;
  * 13º salário em duas parcelas (30/nov e 20/dez), que derrubam o caixa em dezembro.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SEMENTE = 20251031
INICIO, CORTE = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-10-31")
# Vendas e compras começam antes do extrato para a carteira de jan/24 já existir.
GERA_INI = pd.Timestamp("2023-09-01")
RAIZ = Path(__file__).resolve().parents[1]
DADOS = RAIZ / "dados"

# Feriados nacionais (lista fixa; o modelo lê a mesma lista de config/feriados.csv).
FERIADOS = pd.to_datetime([
    "2024-01-01", "2024-02-12", "2024-02-13", "2024-03-29", "2024-04-21", "2024-05-01", "2024-05-30",
    "2024-09-07", "2024-10-12", "2024-11-02", "2024-11-15", "2024-11-20", "2024-12-25",
    "2025-01-01", "2025-03-03", "2025-03-04", "2025-04-18", "2025-04-21", "2025-05-01", "2025-06-19",
    "2025-09-07", "2025-10-12", "2025-11-02", "2025-11-15", "2025-11-20", "2025-12-25",
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-04-03", "2026-04-21", "2026-05-01", "2026-06-04",
    "2026-09-07", "2026-10-12", "2026-11-02", "2026-11-15", "2026-11-20", "2026-12-25",
])
HOL = FERIADOS.values.astype("datetime64[D]")

# segmento: (nº clientes, peso na receita, prazos possíveis, atraso médio, desvio, perda)
SEGMENTOS = {
    "Hospital público":  (8, 0.30, [30],     35, 14, 0.005),
    "Hospital privado":  (14, 0.35, [45, 60], 3, 4, 0.005),
    "Clínica":           (12, 0.15, [28],     6, 8, 0.030),
    "Distribuidor":      (6, 0.20, [60],      2, 3, 0.005),
}
SIGLA = {"Hospital público": "PUB", "Hospital privado": "PRIV", "Clínica": "CLIN", "Distribuidor": "DIST"}
INDICE_MES = {1: .90, 2: .94, 3: 1.02, 4: 1.00, 5: 1.03, 6: 1.00, 7: 1.02, 8: 1.04, 9: 1.01, 10: 1.05,
              11: 1.04, 12: .95}


def util_seguinte(datas: np.ndarray) -> np.ndarray:
    """Leva cada data para o próximo dia útil (ou mantém, se já for)."""
    return np.busday_offset(datas.astype("datetime64[D]"), 0, roll="forward", holidays=HOL)


def dias_uteis(ini, fim) -> pd.DatetimeIndex:
    d = pd.date_range(ini, fim, freq="D")
    return d[np.is_busday(d.values.astype("datetime64[D]"), holidays=HOL)]


def n_util_do_mes(ano: int, mes: int, n: int) -> pd.Timestamp:
    return dias_uteis(pd.Timestamp(ano, mes, 1), pd.Timestamp(ano, mes, 1) + pd.offsets.MonthEnd(0))[n - 1]


def gerar() -> dict:
    rng = np.random.default_rng(SEMENTE)
    DADOS.mkdir(exist_ok=True)

    # ---------------- cadastros ----------------
    clientes = []
    for seg, (n, peso, prazos, atraso, _, _) in SEGMENTOS.items():
        pesos = rng.dirichlet(np.ones(n) * 2) * peso
        for i in range(n):
            clientes.append({"cliente": f"{SIGLA[seg]}-{i + 1:02d}", "segmento": seg,
                             "prazo_dias": int(rng.choice(prazos)), "peso": pesos[i],
                             "atraso_proprio": atraso + rng.normal(0, 3)})
    cli = pd.DataFrame(clientes)
    forn = pd.DataFrame({"fornecedor": [f"FORN-{i + 1:02d}" for i in range(25)],
                         "prazo_dias": rng.choice([28, 42, 56, 70], 25, p=[.2, .35, .3, .15]),
                         "peso": rng.dirichlet(np.ones(25))})

    # ---------------- vendas (contas a receber) ----------------
    dias = dias_uteis(GERA_INI, CORTE)
    anos = (dias - INICIO).days / 365.25
    alvo = 450_000 * (1.08 ** anos) * np.array([INDICE_MES[m] for m in dias.month])
    ticket = 20_000
    n_fat = rng.poisson(alvo / ticket)
    emissao = np.repeat(dias.values, n_fat)
    k = len(emissao)
    idx_cli = rng.choice(len(cli), k, p=cli.peso / cli.peso.sum())
    valor = np.round(rng.lognormal(np.log(ticket) - 0.5 * 0.7 ** 2, 0.7, k), 2)
    c = cli.iloc[idx_cli].reset_index(drop=True)
    venc = util_seguinte(emissao.astype("datetime64[D]") + c.prazo_dias.values.astype("timedelta64[D]"))
    desvio = c.segmento.map({s: v[4] for s, v in SEGMENTOS.items()}).values
    atraso = rng.normal(c.atraso_proprio.values, desvio)
    publico = (c.segmento.values == "Hospital público") & (venc >= np.datetime64("2025-06-01"))
    atraso = np.where(publico, atraso + 20, atraso)
    atraso = np.clip(np.round(atraso), -5, None).astype(int)
    pag = util_seguinte(venc + atraso.astype("timedelta64[D]"))
    perda = rng.random(k) < c.segmento.map({s: v[5] for s, v in SEGMENTOS.items()}).values
    receber = pd.DataFrame({
        "fatura": [f"NF{100000 + i}" for i in range(k)], "cliente": c.cliente, "emissao": emissao,
        "vencimento": venc, "valor": valor, "pagamento": pag,
    })
    receber.loc[perda | (receber.pagamento > CORTE), "pagamento"] = pd.NaT
    receber = receber[~(receber.pagamento < INICIO)].reset_index(drop=True)

    # ---------------- compras (contas a pagar) ----------------
    # Compra ~ 56% da venda prevista para daqui a 30 dias; títulos maiores e menos frequentes.
    alvo_c = 0.56 * 450_000 * (1.08 ** (anos + 30 / 365.25)) * np.array(
        [INDICE_MES[m] for m in (dias + pd.Timedelta(days=30)).month])
    ticket_c = 75_000
    n_c = rng.poisson(alvo_c / ticket_c)
    em_c = np.repeat(dias.values, n_c)
    kc = len(em_c)
    idx_f = rng.choice(len(forn), kc, p=forn.peso)
    f = forn.iloc[idx_f].reset_index(drop=True)
    venc_c = util_seguinte(em_c.astype("datetime64[D]") + f.prazo_dias.values.astype("timedelta64[D]"))
    atraso_c = np.where(rng.random(kc) < 0.10, rng.integers(1, 6, kc), 0)
    pagar = pd.DataFrame({
        "titulo": [f"TP{50000 + i}" for i in range(kc)], "fornecedor": f.fornecedor, "emissao": em_c,
        "vencimento": venc_c,
        "valor": np.round(rng.lognormal(np.log(ticket_c) - 0.5 * 0.5 ** 2, 0.5, kc), 2),
        "pagamento": util_seguinte(venc_c + atraso_c.astype("timedelta64[D]")),
    })
    pagar.loc[pagar.pagamento > CORTE, "pagamento"] = pd.NaT
    pagar = pagar[~(pagar.pagamento < INICIO)].reset_index(drop=True)

    # ---------------- recorrentes ----------------
    mov = []
    meses = pd.period_range(INICIO, CORTE, freq="M")
    vendas_mes = receber.groupby(receber.emissao.dt.to_period("M")).valor.sum()
    for p in meses:
        folha = 1_250_000 * (1.06 if p.year >= 2025 else 1.0)
        mov += [
            (n_util_do_mes(p.year, p.month, 5), "Folha", -folha * rng.normal(1, .01)),
            (util_seguinte(np.array([np.datetime64(f"{p}-20")]))[0], "Encargos", -0.42 * folha * rng.normal(1, .01)),
            (util_seguinte(np.array([np.datetime64(f"{p}-10")]))[0], "Aluguel e serviços", -360_000.0),
        ]
        ant = p - 1
        if ant in vendas_mes.index:
            mov.append((util_seguinte(np.array([np.datetime64(f"{p}-25")]))[0], "Impostos",
                        -0.065 * vendas_mes[ant]))
        if p.month in (3, 6, 9, 12):
            mov.append((util_seguinte(np.array([np.datetime64(f"{p}-15")]))[0], "Empréstimo", -1_500_000.0))
        if p.month == 11:
            mov.append((np.busday_offset(np.datetime64(f"{p}-30"), 0, roll="backward", holidays=HOL),
                        "13º salário", -0.5 * folha))
        if p.month == 12:
            mov.append((np.busday_offset(np.datetime64(f"{p}-20"), 0, roll="backward", holidays=HOL),
                        "13º salário", -0.5 * folha))
    # Despesas diversas: pagamento avulso em ~80% dos dias úteis.
    for d in dias:
        if rng.random() < 0.8:
            mov.append((d, "Despesas diversas", -rng.lognormal(np.log(24_000), 0.6)))
    rec = pd.DataFrame(mov, columns=["data", "categoria", "valor"])
    rec["data"] = pd.to_datetime(rec.data)
    rec = rec[(rec.data >= INICIO) & (rec.data <= CORTE)]

    extrato = pd.concat([
        receber.dropna(subset=["pagamento"]).assign(categoria="Recebimento de clientes")
               .rename(columns={"pagamento": "data", "fatura": "documento"})[["data", "categoria", "documento", "valor"]],
        pagar.dropna(subset=["pagamento"]).assign(categoria="Fornecedores", valor=lambda x: -x.valor)
             .rename(columns={"pagamento": "data", "titulo": "documento"})[["data", "categoria", "documento", "valor"]],
        rec.assign(documento=""),
    ]).sort_values(["data", "categoria"]).reset_index(drop=True)
    extrato["valor"] = extrato.valor.round(2)

    # ---------------- gravação ----------------
    cli.drop(columns=["peso", "atraso_proprio"]).to_csv(DADOS / "clientes.csv", index=False)
    forn.drop(columns=["peso"]).to_csv(DADOS / "fornecedores.csv", index=False)
    for nome, df in (("receber", receber), ("pagar", pagar), ("extrato", extrato)):
        df.to_csv(DADOS / f"{nome}.csv", index=False, date_format="%Y-%m-%d")
    saldo = {"data": "2024-01-01", "saldo": 7_000_000.00}
    (DADOS / "saldo_inicial.json").write_text(json.dumps(saldo, indent=2), encoding="utf-8")
    pd.DataFrame({"data": FERIADOS.strftime("%Y-%m-%d")}).to_csv(RAIZ / "config" / "feriados.csv", index=False)
    return {"faturas_receber": len(receber), "titulos_pagar": len(pagar), "movimentos": len(extrato),
            "saldo_corte": round(saldo["saldo"] + extrato.valor.sum(), 2)}


if __name__ == "__main__":
    print(gerar())
