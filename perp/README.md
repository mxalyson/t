# Sistema preditivo para perp de cripto (Bybit): LONG/SHORT com TP/SL

Modelo LightGBM que estima a probabilidade de um trade com **TP = 3×ATR** atingir o alvo
antes do **SL = 1,5×ATR**, separadamente para long e para short. O sistema opera 10 pares
(BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, LINK, DOT) em velas de **2h**, com saída por tempo
após 48 velas (4 dias).

## Resultados (custos Bybit inclusos)

Custos simulados por trade: taxa taker de 0,055% por lado, slippage de 0,03% por lado
(0,17% ida e volta) e funding de 0,01% a cada 8h, cobrado tanto de long quanto de short.

| Período | Retorno | CAGR | Sharpe | Max DD | Trades/mês | Win rate | PF |
|---|---|---|---|---|---|---|---|
| Desenvolvimento, walk-forward 2019 → set/2025 | +552% | +32%/ano | 1,35 | −23% | 56 | 40% | 1,10 |
| **Holdout cego, out/2025 → out/2026** | **+8,6%** | +8,5% | 0,58 | **−9%** | 28 | 39% | 1,05 |
| Entradas aleatórias, mesmas regras, holdout (200 simulações) | −34% em média (melhor 5%: −9%) | | | | | | |
| Buy & hold no holdout (BTC / ETH / SOL) | −26% / −35% / −43% | | | | | | |

Todos os anos do período de desenvolvimento foram positivos. No holdout o sistema terminou
positivo num mercado em forte queda, e **nenhuma** das 200 carteiras com entradas aleatórias
igualou o resultado. Isso indica que o poder preditivo é real e não sorte. A margem líquida,
porém, é fina: cerca de +8 bps por trade no holdout. **A taxa efetiva e o slippage na Bybit
decidem se o sistema é lucrativo.** Usar ordens limit/maker ou ter desconto VIP melhora
muito o resultado.

## Protocolo contra overfitting e look-ahead

* **Features 100% causais.** São médias e janelas móveis sobre o passado e cross-section
  no mesmo timestamp. A diferença entre calcular as features com o histórico inteiro ou só
  com as últimas 1000 velas é menor que 2e-4.
* **Execução sem look-ahead.** O sinal sai no fechamento da vela t e a entrada é na abertura
  de t+1. Se TP e SL forem tocados na mesma vela, conta como **SL** (pior caso). Gaps além
  do SL saem no preço de abertura.
* **Walk-forward mensal com janela expansiva.** Cada mês é previsto por um modelo treinado só
  com rótulos que terminaram antes do início do mês (purge + embargo de 48 velas).
* **Hiperparâmetros fixos a priori.** Modelo conservador: 15 folhas, mínimo de 400 amostras
  por folha, L2 = 10, sem tuning.
* **Holdout cego.** O período out/2025 → out/2026 não foi usado em nenhuma decisão. A
  configuração foi congelada e commitada (`9dc2112`) **antes** de rodar o holdout uma única vez.
* **Variantes testadas no desenvolvimento.** Foram cerca de 15:
  * timeframes de 1h, 2h e 4h;
  * 5 combinações de barreira;
  * ensemble de barreiras;
  * janela móvel contra janela expansiva;
  * features extras de longo prazo;
  * 2 sementes.

  O 1h foi descartado porque os custos comem a vantagem. O ensemble de barreiras e as
  features extras pioraram o resultado. O 2h e o 4h foram positivos e estáveis entre
  sementes e limiares.

## Arquivos

| Arquivo | Função |
|---|---|
| `lib.py` | Features, simulação triple-barrier, custos e backtest |
| `wf_ml.py` | Walk-forward com purge e embargo |
| `portfolio.py` | Carteira com sizing por risco e limites de exposição |
| `pf_eval.py`, `yearly_pf.py`, `evaluate.py`, `baselines.py` | Avaliação |
| `final_test.py` | Teste único no holdout |
| `config.json` | **Configuração congelada** |
| `models/` | Modelos de produção (treinados até 03/10/2026) |
| `results/` | Trades e curvas de equity do desenvolvimento e do holdout |
| `train_final.py` | Retreino mensal |
| `fetch_history.py` | Download do histórico (Binance spot, mesma fonte do treino) |
| `live.py` | Executor na Bybit |

## Como usar na Bybit

```bash
pip install pandas numpy lightgbm numba pyarrow requests pybit

# 1) Uma vez por mês (dia 1): baixar o histórico e retreinar
python fetch_history.py pq 2h
SEEDS=7,11 python train_final.py pq config.json models

# 2) A cada 2h, 1 minuto após o fechamento da vela (cron UTC: "1 */2 * * *")
python live.py config.json models state.json                          # dry-run (só imprime)
BYBIT_API_KEY=... BYBIT_API_SECRET=... python live.py config.json models state.json --live --testnet
BYBIT_API_KEY=... BYBIT_API_SECRET=... python live.py config.json models state.json --live   # real
```

Regras de carteira:

* cada trade arrisca **0,5% da equity** se bater o SL;
* no máximo **4 posições** abertas, e no máximo 3 líquidas na mesma direção;
* alavancagem de no máximo 2× por posição.

Configure a conta em **one-way mode**.

## Limitações conhecidas

* O treino usa candles **spot** da Binance. As APIs das exchanges estavam bloqueadas no
  ambiente de pesquisa. O perp da Bybit acompanha o spot de perto, mas não exatamente.
* O funding real não foi usado. Ele foi modelado como custo constante cobrado dos dois lados,
  o que é conservador para shorts.
* O desempenho caiu de Sharpe 1,35 no desenvolvimento para 0,58 no holdout. Espere algo
  nessa faixa, não os números do desenvolvimento.
* Antes de usar dinheiro real, rode na testnet ou em dry-run por algumas semanas e compare os
  sinais com o backtest.
