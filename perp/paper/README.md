# Paper trading da v1-congelada

Implementa o protocolo prospectivo do `../VEREDITO.md`. O modelo e as regras não mudam. O motor
registra tudo o que a auditoria pede e se recusa a rodar se `config.json`, `lib.py`, `wf_ml.py` ou
`train_final.py` forem alterados depois do início.

## Instalação (VPS ou PC ligado 24h)

```bash
pip install pandas numpy lightgbm numba pyarrow requests
cd perp
python paper/run.py live --out paper_v1        # primeiro teste manual
```

Não precisa de chave de API: o paper usa só os endpoints públicos da Binance (sinal) e da Bybit
(livro, velas de 1 min e funding do perp).

## Agendamento (crontab, horário UTC)

```
1 */2 * * *  cd /caminho/perp && python paper/run.py live --out paper_v1 >> paper_v1/cron.log 2>&1
30 0 1 * *   cd /caminho/perp && python fetch_history.py pq 2h && SEEDS=7,11 python train_final.py pq config.json models
```

O retreino mensal faz parte da especificação congelada, igual ao walk-forward do backtest. O
hash dos modelos é gravado em cada sinal e em cada trade.

## O que é registrado

| Arquivo | Conteúdo |
|---|---|
| `signals.csv` | Todos os 10 ativos a cada vela: edges, lado, score, ATR, preço spot e a decisão com o motivo (entrada, abaixo do limiar, limite de posições etc.) |
| `trades.csv` | Cada trade fechado (detalhes abaixo) |
| `equity.csv` | Equity realizada e marcada a mercado a cada execução |
| `runs.csv` | Cada execução, com hashes e avisos (por exemplo, velas perdidas) |

Cada linha do `trades.csv` traz:

* sinal e score;
* horários do sinal, da entrada e da saída;
* preço esperado (close spot), mid, bid/ask e preço efetivo;
* spread e slippage;
* TP/SL e motivo da saída;
* taxas, funding real e PnL bruto e líquido.

## Avaliação (após 6 meses e 200 trades, no mínimo)

```bash
python paper/report.py --out paper_v1 --sims 2000
```

O relatório inclui:

* expectativa por trade e em R;
* t Newey-West e bootstrap em blocos;
* Sharpe, profit factor e drawdown marcado a mercado;
* custos observados contra os assumidos no backtest;
* controles pareados B e D;
* concentração por ativo e nos melhores trades;
* decis do score contra o resultado realizado;
* tabela de critérios.

**O relatório não aprova capital real sozinho.**

## Validação feita aqui (offline)

* **Replay do holdout com as previsões auditadas** (`run.py replay --preds`): 337 dos 343 trades
  coincidem com o backtest, e 99,4% saem pelo mesmo motivo. A diferença de retorno vem do funding
  com sinal real (shorts recebem) contra o custo fixo conservador do backtest.
* **Replay com o modelo de produção:** o pipeline completo (features a partir do feed, previsão,
  entradas e saídas) roda sem erro.
* **Trava de versão:** testada.

## Não validado aqui

As chamadas reais às APIs da Binance e da Bybit não puderam ser testadas, porque estão bloqueadas
no ambiente de desenvolvimento. Rode `python paper/run.py live --out teste` uma vez e confira os
arquivos antes de agendar.

## Limitações do paper

* O slippage das saídas por TP/SL (ordens de gatilho) não é observável em paper. Ele é aplicado
  como os 3 bps assumidos e marcado com `exit_slip_observed=False`.
* O slippage de entrada medido é o do topo do livro (meio spread). O impacto de mercado de ordens
  maiores não é capturado. Para medi-lo, rode em paralelo com capital mínimo real ou na testnet.
