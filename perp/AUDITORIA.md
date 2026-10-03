# Auditoria do backtest: sistema preditivo de perp cripto (2h, TP/SL por ATR)

Documento para revisão independente. Descreve o que foi feito, os resultados e os pontos fracos.
O código está em `perp/`. A configuração foi congelada no commit `9dc2112`, antes do teste final.

## 1. Veredito curto

* **Período de desenvolvimento (walk-forward 2019 → set/2025):** passou com folga estatística.
  O retorno líquido por trade é de +20,7 bps (t = 2,56; IC95% bootstrap de +4,7 a +36,8 bps).
* **Holdout cego (out/2025 → out/2026, 12 meses, rodado uma única vez):** lucro de +8,6%,
  Sharpe 0,58 e drawdown máximo de −10% (marcado a mercado). Porém o retorno líquido por
  trade **não é estatisticamente diferente de zero**: +7,7 bps, t = 0,41, IC95% de −27 a +45 bps,
  com 34% de chance bootstrap de a média real ser ≤ 0. O edge **bruto** (+27,8 bps) tem
  t = 1,48 (p ≈ 0,07 unicaudal).
* **Contra entradas aleatórias com as mesmas regras e custos:** −34% em média nas 200 simulações;
  nenhuma chegou a +8,6%. O modelo claramente seleciona melhor que o acaso, mas a margem
  sobre os custos é estreita.
* **Conclusão:** existe sinal preditivo plausível, mas a lucratividade líquida fora da amostra
  ainda não está provada com significância. O passo correto é paper trading ou capital pequeno,
  não capital relevante.

## 2. Dados

| Item | Detalhe |
|---|---|
| Fonte | Candles **spot** da Binance (cache público finom/static-klines no GitHub). As APIs das exchanges estavam bloqueadas no ambiente de pesquisa. |
| Ativos | BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, LINK, DOT (pares USDT) |
| Timeframe | 2h, desde 2017 (cada ativo a partir da sua listagem) |
| Campos | OHLCV, número de trades, volume taker buy |
| Uso pretendido | Perp USDT na Bybit |

## 3. Modelo

* **Rótulo (triple-barrier):** para cada vela t e cada lado (long/short), simula-se uma entrada
  na abertura de t+1 com TP = 3×ATR(14), SL = 1,5×ATR e saída por tempo após 48 velas (4 dias).
  O alvo é 1 se o retorno **líquido de custos** for maior que 0.
* **Modelo:** um LightGBM por lado, com dados agrupados dos 10 ativos e o ativo como variável
  categórica. Hiperparâmetros fixos a priori, sem tuning: 300 árvores, lr 0,03, 15 folhas,
  min_data_in_leaf 400, L2 = 10, feature e bagging fraction de 0,7. Previsão = média de 2 sementes.
* **Features (cerca de 50, todas causais):**
  * retornos de 1 a 256 velas normalizados por volatilidade;
  * volatilidades e razão entre elas;
  * ATR%, RSI, estocásticos de 24/96/288 velas;
  * distância às EMAs 20/50/200 em ATR;
  * z-score de volume e de número de trades;
  * fluxo taker buy;
  * formato da vela;
  * hora e dia da semana;
  * mercado: retorno médio dos 10 ativos e do BTC, breadth;
  * cross-section: rank de retorno 24/72 velas, retorno relativo, beta de 30 dias.
* **Sinal:** edge = p(lado) − taxa base de acerto no treino. Opera o lado de maior edge se
  edge > 0,12.
* **Carteira:**
  * cada trade arrisca 0,5% da equity no SL (notional = 0,5% / SL%, limitado a 2× a equity);
  * no máximo 4 posições abertas e no máximo 3 líquidas na mesma direção;
  * uma posição por ativo;
  * prioridade para os maiores edges.

## 4. Simulação e custos

* O sinal é calculado no fechamento da vela t; a entrada é na **abertura de t+1**.
* TP e SL são verificados com máxima/mínima a partir de t+1. Se ambos forem tocados na mesma
  vela, conta como **SL** (pior caso). Gap na abertura além do SL sai pela abertura.
* Custos por lado: taxa taker da Bybit de 0,055% + slippage de 0,03%, ou seja **0,17% ida e volta**.
* Funding: 0,01% a cada 8h, cobrado de long **e** de short (conservador para shorts).
* Equity: capitalização composta. Recalculada marcada a mercado barra a barra: o DD do holdout
  passa de −9,0% (realizado) para −10,3% (MTM).

## 5. Protocolo contra overfitting e look-ahead

1. **Walk-forward mensal com janela expansiva.** O modelo do mês M treina só com amostras
   cujo rótulo **terminou** antes do início de M menos 48 velas (purge + embargo).
2. **Hiperparâmetros do LightGBM nunca otimizados.**
3. **Holdout separado.** Os 12 meses finais ficaram fora de todas as decisões. A configuração
   foi congelada e commitada antes do único teste.
4. **Robustez verificada no desenvolvimento:**
   * 2 sementes diferentes deram Sharpe de 1,18 a 1,77 entre os limiares;
   * resultados suaves entre os limiares 0,10, 0,12 e 0,15;
   * janela móvel de 3 anos parecida com a expansiva;
   * todos os anos do desenvolvimento foram positivos.
5. **Features ao vivo idênticas às do backtest.** A diferença entre calcular as features com o
   histórico inteiro ou só com as últimas 1000 velas é menor que 2e-4.

**Variantes testadas no desenvolvimento (cerca de 15, todas registradas):**

| Variante | Resultado |
|---|---|
| 1h (3 barreiras) | Prejuízo: os custos comem a vantagem |
| 4h (5 barreiras) | Positivo, Sharpe 0,5–1,3 |
| 2h | Escolhido, Sharpe 1,2–1,7 |
| Ensemble de barreiras | Piorou |
| Features de prazo longo | Pioraram |
| Janela móvel | Neutro |

Escolher o melhor entre cerca de 15 variantes infla o resultado do desenvolvimento. Por isso
só o holdout deve ser usado para julgar o sistema.

## 6. Resultados

| Período | Retorno | CAGR | Sharpe | Max DD | Trades | Trades/mês | Win rate | PF | Líquido/trade |
|---|---|---|---|---|---|---|---|---|---|
| Dev 2019 → set/2025 | +552% | +32% | 1,35 | −23% | 4569 | 56 | 40% | 1,10 | +20,7 bps |
| **Holdout out/2025 → out/2026** | **+8,6%** | +8,5% | **0,58** | **−10%** | 343 | 28 | 39% | 1,05 | +7,7 bps |
| Aleatório no holdout (200 sims) | −34% em média, melhor 5%: −9% | | | | | | | | |
| Buy & hold BTC / ETH / SOL no holdout | −26% / −35% / −43% | | | | | | | | |

**Ano a ano no desenvolvimento:**

| Ano | Retorno | Sharpe |
|---|---|---|
| 2019 | +22% | 1,0 |
| 2020 | +25% | 1,0 |
| 2021 | +44% | 1,8 |
| 2022 | +49% | 1,9 |
| 2023 | +14% | 0,7 |
| 2024 | +46% | 1,9 |
| 2025 (até set) | +20% | 1,2 |

**Holdout por mês** (bps médios por trade):

| Mês | 10/25 | 11/25 | 12/25 | 01/26 | 02/26 | 03/26 | 04/26 | 05/26 | 06/26 | 07/26 | 08/26 | 09/26 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bps/trade | −5 | +215 | −55 | +31 | −155 | +106 | −74 | +22 | +33 | +47 | +52 | −26 |

No holdout, os longs renderam +12 bps por trade e os shorts +4 bps. O resultado ficou concentrado
em AVAX, LINK e SOL; DOGE, XRP e ETH foram negativos.

## 7. Pontos fracos conhecidos

1. **Significância fora da amostra:** com 343 trades, o lucro líquido do holdout não é
   estatisticamente significativo (t = 0,41).
2. **Viés de sobrevivência e seleção do universo:** os 10 ativos são os maiores **de hoje**.
   Moedas que morreram (LUNA, FTT etc.) não estão no teste. Isso favorece o desenvolvimento
   e, em menor grau, o holdout.
3. **Spot em vez de perp:** treino e backtest usam spot da Binance; a execução seria em perp na
   Bybit, onde basis, wicks e liquidez diferem.
4. **Funding modelado como constante,** sem a série histórica real.
5. **Slippage fixo de 3 bps por lado.** Para entradas a mercado logo após o fechamento da vela
   em alts, pode ser otimista em momentos de volatilidade.
6. **Seleção entre cerca de 15 variantes no desenvolvimento.** O Sharpe de 1,35 do dev está
   inflado; a queda para 0,58 no holdout é coerente com isso.
7. **Margem fina:** custos de cerca de 20 bps contra edge bruto de cerca de 28 bps no holdout.
   Pequenas piorias de execução eliminam o lucro.
8. **Um único período de holdout,** num mercado de queda. É pouco para conclusões sobre regimes.

## 8. Perguntas sugeridas para o revisor

* O purge/embargo (rótulo precisa terminar antes de início do mês − 48 velas) é suficiente?
* A regra "TP e SL na mesma vela = SL" e a entrada na abertura de t+1 eliminam o look-ahead
  na execução?
* As features cross-section calculadas no mesmo timestamp (rank, média de mercado, beta) são
  causais na prática, já que todas as velas fecham no mesmo instante?
* Como medir o efeito do viés de sobrevivência do universo?
* O que seria evidência suficiente em paper trading para usar capital real (número de trades,
  t-stat, comparação de slippage)?
