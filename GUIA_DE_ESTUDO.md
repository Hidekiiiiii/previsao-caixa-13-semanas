# Guia de estudo: a previsão de caixa por dentro

Objetivo: explicar o projeto numa entrevista, defender as escolhas de método e fazer pequenas mudanças com segurança. Sempre que possível, a comparação é com o que você já faz no Excel.

---

## 1. O problema em uma frase

"Quanto vou ter no banco em cada uma das próximas 13 semanas, e quanto atraso dos clientes eu aguento antes de ficar abaixo do caixa mínimo?"

- **Método direto:** soma entradas e saídas previstas, linha a linha (recebimentos, fornecedores, folha, impostos). É o que a tesouraria usa no curto prazo.
- **Método indireto:** parte do lucro e ajusta pelas variações de capital de giro. Serve para o mês ou o ano, não para dizer em que semana o dinheiro falta.
- **13 semanas:** o padrão de mercado para caixa de curto prazo. Cobre um trimestre e ainda dá para agir: negociar prazo, antecipar recebível, adiar compra.

## 2. Mapa dos arquivos

| Arquivo | Papel | Analogia no Excel |
|---|---|---|
| `gerador/gerar_dados.py` | Cria a base fictícia do Grupo Atlas (semente fixa `20251031`) | Quem preenche a planilha de exemplo |
| `config/parametros.json` | Caixa mínimo, horizonte, janelas e regras de calendário | Aba de premissas |
| `caixa/dados.py` | Lê os CSVs numa `Base`; `Calendario` sabe o que é dia útil | Aba de dados + `DIATRABALHO` |
| `caixa/previsao.py` | O motor: curvas, vendas futuras e as 13 semanas | Aba de cálculo |
| `caixa/backtest.py` | Refaz a previsão no passado e mede o erro | Aba "previsto × realizado" |
| `caixa/cenarios.py` | Simula atraso e calcula a folga em dias | Atingir meta / tabela de dados |
| `caixa/relatorio.py`, `graficos.py` | Excel de 5 abas e os PNG de `docs/` | Formatação e gráficos |
| `caixa/api.py` + `caixa/web/` | API FastAPI e as duas páginas | Uma planilha que outros sistemas consultam |
| `caixa/__main__.py` | `python -m caixa`: roda tudo e imprime o resumo | O botão "Atualizar tudo" |

`previsao.py`, `backtest.py` e `cenarios.py` não sabem que a API existe. Por isso dá para testá-los sem servidor, e a API só traduz para JSON o que o motor calcula.

## 3. O caminho de um número: o saldo da semana 13

1. `carregar()` (`caixa/dados.py`) lê `receber.csv`, `pagar.csv`, `extrato.csv` e o saldo inicial, e junta o segmento do cliente em cada fatura.
2. `prever(base, "2025-10-31")` (`caixa/previsao.py`, linha 144) monta uma lista de movimentos **diários**, cada um com data, linha do fluxo e valor. As seis partes do código estão marcadas `# 1)` a `# 6)`.
3. A função interna `junta()` empurra cada data para o próximo dia útil (`cal.rolar`) e descarta o que cai fora das 13 semanas.
4. `_semana()` converte a data em número de semana: S1 = 01/11 a 07/11, e assim por diante.
5. `Previsao.semanal()` faz uma **tabela dinâmica** (linha × semana) e acrescenta saldo inicial, fluxo líquido e saldo final acumulado.
6. Saldo inicial de R$ 8,6 mi (o extrato em 31/10) mais 13 semanas de fluxo dá **R$ 11,5 mi na S13**. O menor saldo, **R$ 7,6 mi**, fica na S1.

## 4. A curva de recebimento (Kaplan-Meier)

A curva responde: "de cada R$ 100 que vencem, quanto já entrou *x* dias depois do vencimento?". Ela é calculada por segmento (hospital público, hospital privado, clínica, distribuidor), com as faturas que venceram nos últimos 240 dias.

**O problema:** muitas dessas faturas ainda não foram pagas. Há dois jeitos ingênuos de lidar com elas, e os dois erram:
- **Ignorar as não pagas:** a curva fica otimista, porque justamente quem está atrasando agora some da conta.
- **Tratar as não pagas como "nunca vão pagar":** a curva fica pessimista.

**Kaplan-Meier** usa a fatura em aberto enquanto ela é informação ("até o dia 15 ainda não tinha pagado") e depois a tira da conta. Isso se chama **censura**. O termo vem da estatística de sobrevivência, onde a pergunta é "quanto tempo até o evento".

**Exemplo à mão (5 faturas):** quatro foram pagas com 0, 10, 10 e 30 dias de atraso. A quinta está em aberto, com 15 dias de atraso.

| Dia | Em risco (ainda não pagas) | Pagaram no dia | Fração que continua sem pagar | S (acumulado) |
|---|---|---|---|---|
| 0 | 5 | 1 | 4/5 | 0,80 |
| 10 | 4 | 2 | 2/4 | 0,80 × 0,5 = **0,40** |
| 15 | 2 | 0 (a fatura em aberto sai da conta aqui) | 2/2 | 0,40 |
| 30 | 1 | 1 | 0/1 | 0,00 |

Resultado: até o dia 10 entraram **60%** (1 − 0,40). Se ignorasse a fatura em aberto, a conta seria 3 de 4 = **75%**, otimista demais. Os números da tabela foram conferidos com a própria função `curva_sobrevivencia`.

No código (`previsao.py`, linha 37):
- `S` é a "fração que ainda não pagou" em cada dia, de −10 a 400 dias (`GRADE`). O −10 cobre quem paga antes do vencimento.
- `em_risco` é a contagem regressiva das faturas que chegaram vivas até aquele dia.
- `h = eventos / em_risco` é a chance de pagar *naquele dia*, dado que não pagou antes.
- `np.cumprod(1 - h)` é a multiplicação acumulada da tabela acima, como um `PRODUTO` acumulado no Excel.

`pmf_atraso(S)` (linha 64) faz a diferença entre dias seguidos da curva. O resultado é a probabilidade de pagar **exatamente** no dia *x*. O que sobra depois de 400 dias é a perda.

**Curvas reais da base (31/10/25):**

| Segmento | Pago até o vencimento | Até 30 dias | Até 60 dias | Nunca |
|---|---|---|---|---|
| Hospital público | 0,4% | 15,6% | 77,6% | 0,2% |
| Hospital privado | 28,1% | 99,7% | 99,7% | 0,3% |
| Distribuidor | 25,0% | 99,1% | 99,1% | 0,9% |
| Clínica | 21,4% | 96,6% | 97,4% | 2,6% |

Hospital público paga tarde mas paga. Clínica paga rápido, mas parte nunca paga.

## 5. Fatura já vencida: probabilidade condicional

Uma fatura de hospital público vencida há 20 dias não pode mais ser paga no dia 5. A pergunta muda para: "**dado que não pagou até hoje**, quando paga?".

```
P(pagar no dia d | ainda não pagou no dia k) = pmf(d) / S(k),   só para d > k
```

No código (linhas 184 a 188):
- `k` é quantos dias a fatura já está vencida.
- `pmf * (GRADE > k)` zera os dias que já passaram.
- A divisão por `S_k` redistribui a probabilidade restante.

É a mesma lógica de "percentual do que sobrou" que se usa em aging de provisão.

Consequência: uma fatura vencida há 300 dias tem S(k) muito baixo e pouca chance de entrar. Assim o modelo trata como perda provável o que está muito velho, sem precisar de uma regra de "acima de 90 dias".

## 6. Vendas futuras: nível × sazonalidade, depois prazo × atraso

As faturas que ainda não existem também viram caixa dentro das 13 semanas. São três passos.

**a) Quanto vou vender por dia útil** (`prever_vendas`, linha 76):
- **Índice do mês:** a média por dia útil de cada um dos últimos 12 meses fechados, dividida pela média geral (1,10 = mês 10% acima).
- **Nível:** a média dos últimos 40 dias úteis, **dividida** pelo índice desses dias. Isso tira a sazonalidade antes de projetar.
- **Previsão:** nível × índice do mês futuro. É a mesma decomposição que você faria no Excel com uma tabela de índices sazonais.

**b) Para qual segmento:** o mix dos últimos 90 dias (linha 202).

**c) Quando o dinheiro entra:** a data da venda + o prazo concedido + o atraso do cliente. Como prazo e atraso são incertos, o código combina as duas distribuições com `np.convolve`: a **convolução**.

**Exemplo:** prazo de 30 dias (metade das vendas) ou 60 dias (outra metade); atraso de 0 (50%) ou 10 dias (50%).

| Prazo \ Atraso | 0 dias (50%) | 10 dias (50%) |
|---|---|---|
| 30 dias (50%) | dia 30: 25% | dia 40: 25% |
| 60 dias (50%) | dia 60: 25% | dia 70: 25% |

A convolução faz isto: todas as combinações, multiplica as probabilidades e soma pelo dia total. No Excel seria uma tabela de dupla entrada com `SOMASES` pelo dia resultante.

## 7. As outras linhas do fluxo

| Linha | Regra | Onde |
|---|---|---|
| Fornecedores em aberto | Paga no vencimento. O que já venceu sai no dia seguinte. | `# 3)` |
| Compras futuras | Vendas previstas × (compras ÷ vendas dos últimos 90 dias, **58%** hoje), no prazo médio dos fornecedores | `# 3)` |
| Folha, encargos, aluguel, empréstimo, 13º | Regras de `config/parametros.json` com o último valor pago | `# 4)` |
| Impostos | Mediana das 3 últimas alíquotas efetivas (**6,5%**) × vendas do mês anterior, no dia 25 | `# 5)` |
| Despesas diversas | Média por dia útil das últimas 13 semanas | `# 6)` |

**Calendário:** `Calendario` usa `np.busday_offset` e a lista de feriados, como `DIATRABALHO` do Excel. Na data-base de 31/10/25:
- **Folha (5º dia útil):** 07/11, 05/12 e 08/01. Em janeiro o dia 1º é feriado, por isso cai no dia 8.
- **13º salário:** 30/11 cai num domingo e vira 28/11; 20/12 cai num sábado e vira 19/12. Pela regra, se cair em fim de semana, antecipa (`"ajuste": "anterior"`).

## 8. O backtest: como provar que o modelo presta

Previsão sem teste contra o passado é opinião. `backtest.rodar()` faz o seguinte:
1. Escolhe **31 sextas-feiras** de 2025 cujas 13 semanas seguintes já estão no extrato (`datas_base`).
2. Em cada uma, roda a previsão **como se fosse aquele dia**.
3. Compara com o extrato real das 13 semanas seguintes.

**Três concorrentes:**
- **Curvas:** o modelo.
- **Planilha no vencimento:** todo cliente paga no vencimento. O vencido há até 90 dias entra no dia seguinte; o mais velho vira perda. **As outras linhas são iguais às do modelo**, então a diferença entre os dois mede só o valor das curvas.
- **Média de 13 semanas:** o ingênuo. Toda semana futura é igual à média das 13 últimas.

**Métricas** (`resumo`, linha 75):
- **WAPE** = soma dos erros absolutos ÷ soma do realizado. É um "erro percentual" que não explode em semana de valor pequeno, o que acontece com o MAPE.
- **Erro médio absoluto do saldo na S4 e na S13**, em reais.
- **Viés:** a média do erro *com* sinal. Mostra se o método erra sempre para o mesmo lado.

| Método | WAPE recebimentos | Erro saldo S4 | Erro saldo S13 |
|---|---|---|---|
| Curvas | 9,1% | R$ 0,5 mi | R$ 1,3 mi |
| Planilha | 26,7% | R$ 5,1 mi | R$ 5,9 mi |
| Média 13 semanas | 10,6% | R$ 1,1 mi | R$ 2,6 mi |

**O vazamento de informação.** O maior risco de um backtest é a previsão "espiar" o futuro sem querer. Por exemplo: usar o campo `pagamento` de uma fatura paga *depois* da data-base para montar a curva. Para evitar isso:
- `curvas_por_segmento` trata como **em aberto** quem pagou depois de `t` (linha 57).
- O teste `test_nao_usa_informacao_posterior_a_data_base` apaga tudo o que aconteceu depois de `t` e exige uma previsão **idêntica**.
- Esse teste foi validado por mutação: com o vazamento introduzido de propósito, ele falhou.

**Onde o modelo erra, e por quê:** o viés na S13 é de cerca de R$ 1,1 mi para cima. Em jun/25 os hospitais públicos passaram a pagar cerca de 19 dias mais tarde, e a curva de 240 dias demora para incorporar isso. Admitir o erro e explicar a causa vale mais na entrevista do que dizer que acertou.

## 9. Cenários e folga

`prever(..., atraso_extra={"Hospital público": 30})` desloca em 30 dias toda a curva do segmento, tanto da carteira quanto das vendas futuras (`d = extra.get(seg, 0)`).

A **folga** (`folga_em_dias`, `cenarios.py`) é o maior atraso extra que ainda mantém o caixa acima de R$ 6 mi nas 13 semanas. Em vez de testar 0, 1, 2… até 120, o código usa **busca binária**:
1. Testa o meio (60).
2. Se fura, testa 30; se não fura, testa 90.
3. Repete, sempre cortando o intervalo ao meio.

Bastam umas 7 previsões em vez de 120. Isso só é válido porque **mais atraso nunca melhora o caixa**, e há um teste para essa premissa (`test_mais_atraso_nunca_melhora_o_caixa`). É o "Atingir meta" do Excel, mas com garantia de achar o limite exato (`test_folga_e_o_limite_exato`).

Resultado em 31/10/25:

| Segmento | Folga |
|---|---|
| Hospital público | 22 dias |
| Hospital privado | 21 dias |
| Distribuidor | 38 dias |
| Clínica | 120 dias ou mais (o teto da busca) |

## 10. A API e as páginas

- **FastAPI:** cada função com `@app.get("/rota")` vira um endereço. Os parâmetros da função viram parâmetros da URL (`?data_base=2025-10-31`).
- **Pydantic** (`class Semana(BaseModel)` etc.): descreve o formato de cada resposta. O FastAPI valida a saída e gera a documentação em `/docs` sozinho.
- **Validação:**
  - Uma data inválida, um `metodo` diferente de `curvas`/`vencimento` ou um atraso negativo devolvem **422**, com a mensagem de erro.
  - O dia 31/02 é recusado pelo próprio tipo `date`.
  - As regras de negócio (data fora do histórico, segmento inexistente) ficam em `_data()` e `_valida_segmentos()`.
- **Cache (`@lru_cache`):** a base é lida uma vez (`_base`). A folga e o backtest, que são lentos, ficam guardados depois da primeira chamada. O risco é o mesmo de qualquer cache: se os CSVs mudarem com o servidor no ar, é preciso reiniciar.
- **Páginas:** `web/tec.html` (a principal) e `web/index.html` (`/classica`) são HTML + JavaScript puro, sem bibliotecas. Elas chamam `/previsao`, `/cenario` e `/folga` com `fetch` e desenham o gráfico em SVG.

## 11. Os testes

`python -m pytest`: 27 testes, 15 do motor (`tests/test_caixa.py`) e 12 da API (`tests/test_api.py`). Eles verificam quatro tipos de coisa:

- **Identidades contábeis:**
  - o saldo inicial + os movimentos = o saldo final;
  - o que a carteira espera receber no horizonte + o que não espera = o total em aberto.
- **Casos conhecidos:**
  - sem faturas em aberto, Kaplan-Meier tem de dar a proporção simples;
  - folha, 13º, empréstimo e impostos caem nas datas certas.
- **Propriedades:**
  - mais atraso nunca melhora o caixa;
  - a curva nunca cresce;
  - as probabilidades somam no máximo 1.
- **API contra o motor:** a resposta JSON bate com a chamada direta de `prever()`, e as entradas inválidas devolvem 422.

## 12. Cinco exercícios

Depois de cada um, rode `python -m pytest` e desfaça a mudança. Os resultados citados foram conferidos rodando o código.

1. **Subir o caixa mínimo (fácil).** Em `config/parametros.json`, troque `"saldo_minimo": 6000000` por `7500000` e rode `python -m caixa --sem-backtest`. O cenário base continua sem alerta (o menor saldo é R$ 7,64 mi), mas a folga dos hospitais públicos cai para **0 dia**: qualquer atraso já fura. Explique por quê.
2. **Encurtar a memória da curva (médio).** Troque `"janela_curvas_dias": 240` por `90`. O saldo da S13 cai de R$ 11,5 mi para cerca de **R$ 10,4 mi**, porque a curva passa a enxergar só o período recente, em que os públicos pagam mais tarde. Discuta o dilema: com janela curta, o modelo reage rápido a mudanças, mas tem menos dados e mais ruído.
3. **Acrescentar uma saída fixa (médio).** Em `"recorrentes"`, inclua `{"categoria": "Aluguel e serviços", "regra": "dia", "dia": 25, "ajuste": "seguinte"}`. O aluguel passa a sair duas vezes por mês, e a linha dobra no Excel de saída. Mesmo assim, **os 15 testes do motor continuam passando**: `test_calendario_das_saidas_fixas` só confere folha, 13º, empréstimo e impostos. Lição: teste verde só garante o que ele confere. Escreva o `assert` que pegaria esse erro.
4. **Quebrar a censura de propósito (médio).** Em `curvas_por_segmento` (linha 57), troque `pago = obs.pagamento.notna() & (obs.pagamento <= data_base)` por `pago = obs.pagamento.notna()`. Rode os testes: o de vazamento de informação deve falhar. Agora você sabe o que ele protege.
5. **Testar a busca binária (fácil).** No terminal, rode `python -c "from caixa.dados import carregar; from caixa.cenarios import simular; print(simular(carregar(), '2025-10-31', {'Hospital público': 22}))"` e depois com `23`.
   - Com 22 dias, o menor saldo é R$ 6,13 mi, na S8, e não há alerta.
   - Com 23 dias, cai para R$ 5,99 mi, e a S8 fura o mínimo.

## 13. Perguntas de entrevista

1. **Por que método direto e não indireto?** Para 13 semanas a pergunta é "em que semana falta dinheiro". Só o direto mostra a data de cada entrada e saída.
2. **O que é Kaplan-Meier e por que usar?** É a curva de "quanto tempo até o evento" que aproveita os casos ainda em aberto (censurados). Sem ela, as faturas que estão atrasando somem da amostra, e a curva fica otimista.
3. **Como você trata uma fatura que já está vencida?** Uso a curva condicionada: só a parte depois do atraso atual, renormalizada. Quanto mais velha, menor a chance de entrar, e isso vira perda provável sem regra arbitrária.
4. **Como sabe que o modelo é melhor que a planilha?** Pelo backtest em 31 datas, sem informação do futuro: o erro do saldo na S13 cai de R$ 5,9 mi para R$ 1,3 mi. E ele ganha também do método ingênuo da média.
5. **Como garante que o backtest não trapaceia?** Um teste apaga tudo depois da data-base e exige o mesmo resultado. Validei que ele pega o vazamento, introduzindo o erro de propósito.
6. **Onde o modelo erra?** Ele demora a captar mudança de comportamento: os públicos passaram a atrasar mais em jun/25, e o saldo ficou superestimado. O próximo passo seria um alerta de "atraso recente acima da curva".
7. **O que é a folga e como você calcula?** É o atraso extra máximo antes de furar o mínimo, calculado por busca binária. A busca é válida porque mais atraso nunca melhora o caixa, e isso é testado.
8. **Por que uma API?** Para que um sistema de tesouraria ou um painel consuma a previsão sem abrir planilha. A lógica fica num lugar só, e a API só formata e valida.
9. **O que mudaria com dados reais?** Curva por cliente grande, feriados municipais, faixas de confiança (simulação fatura a fatura) e uma conciliação do extrato antes de rodar.
10. **Como você usou IA no projeto?** Escreva sua resposta: o que você definiu (método, linhas do fluxo, critério de "pronto"), o que conferiu, e como o backtest e os testes validaram o resultado.
