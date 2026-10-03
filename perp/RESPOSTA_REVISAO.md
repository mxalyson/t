# Resposta à auditoria independente: testes complementares executados

Nada no modelo congelado foi alterado. Todos os testes usam as previsões out-of-sample já gravadas,
mudando só a avaliação. Código: `perp/audit.py` e `perp/test_lookahead.py`. Resultados brutos:
`perp/results/audit_holdout.json` e `perp/results/audit_dev.json`.

Concordo com o veredito da revisão: **não aprovado para capital relevante; apto para paper trading
instrumentado.** Os testes abaixo reforçam esse veredito e mostram de onde vem o sinal.

## 1. Vazamento temporal: testes automáticos

| Teste | Resultado |
|---|---|
| Perturbar todos os preços e volumes após T e recalcular as features | Nenhuma feature em ts ≤ T muda (tolerância 1e-12) |
| Perturbar os dados após o fim do mês de teste (mar/2024) e rodar o walk-forward | Previsões do mês idênticas |
| Purge: último rótulo usado no treino de mar/2024 | Termina às 22:00 de 25/02, antes do corte de 00:00 de 26/02 (início do teste − 48 velas) |
| Velas faltantes | 6 a 23 em cerca de 30 mil por ativo (quedas da própria Binance). Sem preenchimento nem interpolação; rank e mercado usam só os ativos presentes |
| Universo do rank | Só ativos listados no instante; o rank ignora os ausentes |

## 2. Estatística robusta à dependência (unidade = carteira, não trade)

| | Dev 2019 → set/2025 | Holdout out/25 → out/26 |
|---|---|---|
| t diário, Newey-West (10 lags) | 3,46 | 0,62 |
| t semanal, Newey-West (4 lags) | 3,75 (352 semanas) | 0,64 (52 semanas) |
| Trades agrupados por semana, t (Newey-West) | 2,18 | 0,36 |
| Bootstrap em blocos de 10 dias: P(média ≤ 0) | 0,08% | 32% |
| Bootstrap em blocos de 20 dias: IC95% do retorno anual | +15% a +48% | −21% a +32% |

**Conclusão:** o dev é significativo mesmo com correção de dependência. O holdout não é: o
número de observações independentes é pequeno (52 semanas) para um edge desse tamanho.

## 3. Controles aleatórios pareados (2000 simulações no holdout, 100 no dev)

Todos os controles usam a mesma carteira, os mesmos custos, o mesmo TP/SL, o mesmo sizing e
os mesmos limites de exposição.

| Controle | Holdout: retorno médio | Holdout: percentil do modelo | Dev: percentil do modelo |
|---|---|---|---|
| B — lado aleatório, mesmos timestamps e ativos | −18,5% | **99,6%** | 100% |
| C — ativo aleatório, mesmo timestamp e lado | +6,5% | **60%** | 99% |
| D — scores embaralhados entre ativos e datas | −32,5% | **99,5%** | 100% |

**Interpretação:**

* A **direção** (B) e a **ordenação por edge** (D) têm valor fora da amostra.
* A **escolha do ativo** (C) teve valor no dev, mas não no holdout. Em 2025/26, o ganho veio do
  **momento e da direção** da entrada (componente de mercado), não de escolher a moeda certa.

## 4. Baselines simples (mesma frequência de sinais e mesma carteira)

O limiar de cada baseline foi calibrado só com dados anteriores ao período de teste.

| Estratégia | Holdout | Dev |
|---|---|---|
| Modelo | +8,6% | CAGR +32% |
| Momentum de 1 semana | −8,0% | CAGR −4,5% |
| Reversão de 1 dia | −27,1% | CAGR −58% (−99,7% no total) |

## 5. Edge contra expectativa realizada (pergunta sobre "edge ≠ EV")

**Retorno líquido realizado por decil do edge** (todos os candles, não só os operados):

| Decil | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Dev (bps) | −30 | −15 | −13 | −7 | −3 | 0 | 0 | +5 | +18 | +26 |
| Holdout (bps) | −20 | −25 | −19 | −19 | −25 | −2 | +3 | +4 | +8 | +1 |

* **Monotonicidade (Spearman entre decil e retorno):** 0,99 no dev e 0,79 no holdout.
* **Faixa operada no holdout:** edge entre 0,12 e 0,16 rendeu +10,6 bps com 42% de acerto.
* **Leitura:** o modelo separa bem os trades **ruins** (a metade inferior perde cerca de 20 bps em
  ambos os períodos). No holdout, o topo ficou só levemente positivo depois dos custos.

**Calibração:**

| | Brier modelo | Brier taxa base | Log loss modelo | Log loss taxa base |
|---|---|---|---|---|
| Holdout long | 0,2157 | 0,2150 | 0,6235 | 0,6215 |
| Holdout short | 0,2373 | 0,2355 | 0,6681 | 0,6639 |
| Dev long | 0,2248 | 0,2240 | 0,6425 | 0,6403 |

**Achado importante:** as probabilidades **não são calibradas**. Brier e log loss ficam ligeiramente
**piores** que a taxa base, inclusive no dev. O modelo serve como **ranking**, não como probabilidade.
A crítica da revisão procede: "edge = p − base" não pode ser lido como expectativa. A recomendação
é calibrar (isotônica no treino de cada fold) e ranquear por EV estimado. Isso seria uma nova
versão e exige nova validação prospectiva.

## 6. Dependência de ativos e de caudas

**Remoção de ativos (sem retreinar):**

| Removido | Holdout | Dev (CAGR) |
|---|---|---|
| Nenhum | +8,6% | +32% |
| Sem AVAX | +5,1% | +28% |
| Sem LINK | +5,0% | +29% |
| Sem SOL | +6,5% | +30% |
| Sem os três | **−0,8%** | +25% |

**Remoção dos melhores trades** (o PnL desses trades é zerado; os demais ficam iguais):

| Removidos | Holdout | Dev (CAGR) |
|---|---|---|
| Top 1% | +4,5% | +24% |
| Top 2% | +1,5% | +16% |
| Top 5% | **−8,3%** | −4,5% |

**Novembro/2025** (+215 bps por trade): 24 trades. Os 5 maiores renderam 1039, 1008, 991, 829 e
770 bps (AVAX, DOT, LINK, SOL). Sem eles, o mês fica em +27 bps por trade.

**Interpretação:** é um sistema de payoff assimétrico (TP = 2× SL, acerto de cerca de 40%). Por
construção, o lucro vem da cauda direita. No holdout, porém, essa cauda ficou concentrada em
poucos ativos e num único episódio, o que é frágil.

## 7. Estresse de execução

| Cenário | Holdout | Dev (CAGR) |
|---|---|---|
| Base (3 bps/lado) | +8,6% | +32% |
| Slippage 6 bps/lado | +2,9% | +21% |
| Slippage 10 bps/lado | **−4,3%** | +7,8% |
| Entrada atrasada 1 vela (2h) | **+11,8%** | +13% |
| Atraso de 1 vela + slippage 6 bps | +5,5% | +3,0% |

* **Atraso:** o sistema não depende do preço exato da abertura seguinte. Com uma vela de atraso,
  ele continua positivo nos dois períodos, o que descarta um artefato de microestrutura.
* **Slippage:** é o principal risco. A partir de cerca de 8 bps por lado, o holdout vira prejuízo.

## 8. Não executado (dados indisponíveis no ambiente de pesquisa)

* Funding histórico real da Bybit.
* Spread bid-ask observado.
* Universo point-in-time com moedas deslistadas (LUNA, FTT etc.).

Os três ficam como pré-requisito antes de qualquer capital real. O funding e o slippage reais
podem ser medidos no paper trading.

## 9. Esclarecimentos

* **Janela do holdout:** de 01/10/2025 00:00 a 03/10/2026 08:00 UTC (último dado disponível).
  Outubro/2026 tem 1 trade fechado, já contado nos 343.
* **Trades por mês:** 40, 24, 27, 45, 28, 14, 31, 29, 32, 17, 26, 29, mais 1 em outubro/26.
* **CAGR:** equity final^(365,25/dias) − 1. Dev: 6,52× em 6,75 anos = 32%/ano.
  Holdout: 1,086× em 1,01 ano = 8,5%/ano.
* **Taxa base:** calculada só com o conjunto de treino de cada fold.
* **Custo de 0,17% ida e volta:** taker + slippage na entrada e na saída, incluindo saídas por SL
  (executadas no preço do stop menos o slippage, ou na abertura em caso de gap). Execução parcial
  não foi modelada.
* **Funding:** 0,01% × (velas mantidas × 2h / 8h).
* **Marcação a mercado:** pelo fechamento de cada vela de 2h, não por preço executável.

## 10. Conclusão atualizada

| Hipótese | Evidência |
|---|---|
| 1. Prevê melhor que entradas aleatórias | **Sim.** Controles pareados B e D no percentil 99,5 a 99,6 do holdout |
| 2. Seleciona trades com retorno bruto positivo | **Provável.** Bruto de +27,8 bps no holdout (t = 1,48), decis monotônicos |
| 3. Lucro líquido após custos reais em diferentes regimes | **Não demonstrado.** t Newey-West de 0,6 no holdout; depende de 3 ativos e de cerca de 2% dos trades; vira negativo com 10 bps de slippage |

**Próximo passo:** paper trading prospectivo com o modelo **sem nenhuma alteração**. Critérios
definidos antes de começar:

* no mínimo 6 meses e 200 trades fechados;
* slippage e funding reais medidos;
* expectativa líquida maior que 0;
* no máximo 50% do PnL vindo de um único ativo;
* resultado acima dos controles pareados B e D.

Uma versão 2 (probabilidades calibradas, ranking por EV, universo point-in-time) pode ser
desenvolvida em paralelo, mas só seria comparada usando o mesmo período prospectivo.
