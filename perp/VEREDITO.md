# Veredito final da auditoria: sistema perp 2h (v1-congelada)

## Decisão: aprovado para paper trading, não aprovado para capital real relevante

Após os testes complementares, o sistema apresenta evidência consistente de capacidade preditiva,
mas ainda não demonstrou uma vantagem econômica líquida suficientemente robusta para justificar
exposição financeira relevante. A versão atual fica congelada, e sua capacidade de gerar resultados
será testada de forma prospectiva, sob condições reais de execução.

## 1. Demonstrado

1. Os testes de perturbação temporal não identificaram vazamento nas features e previsões examinadas.
2. O modelo supera os controles aleatórios pareados de direção (B) e de ordenação (D) no holdout.
3. O ranking separa operações ruins de oportunidades relativamente melhores.
4. O resultado não depende do preço exato de abertura da vela seguinte.
5. O período de desenvolvimento tem evidência estatística de retorno positivo, mesmo com correção
   de dependência temporal.

## 2. Não demonstrado

1. Expectativa líquida positiva fora da amostra com significância estatística. O holdout teve +8,6%,
   mas t Newey-West de 0,62.
2. Calibração confiável das probabilidades.
3. Robustez ao slippage real de execução na Bybit.
4. Generalização da seleção de ativos.
5. Robustez ao funding histórico e ao spread bid-ask.
6. Independência em relação a poucos eventos extremos.
7. Rentabilidade consistente em diferentes regimes de mercado.

## 3. Versão congelada: não modificar

Nada da lista abaixo pode mudar:

* features;
* hiperparâmetros;
* threshold de 0,12;
* TP/SL (3 / 1,5 ATR) e H = 48;
* sizing (0,5% de risco, 4 posições, 3 líquidas, 2× de alavancagem);
* universo de 10 ativos;
* regras de entrada e saída.

O retreino mensal com `train_final.py` faz parte da especificação, igual ao walk-forward do
backtest, e não conta como alteração. Qualquer outra mudança gera uma nova versão, com
identificação própria e validação independente.

O `paper/engine.py` verifica o hash dos arquivos congelados e se recusa a rodar se algum deles
mudar.

## 4. Protocolo prospectivo

* **Duração:** no mínimo 6 meses e 200 trades fechados, de preferência 300 a 500. Os resultados
  não podem ser usados para reajustar a versão.
* **Registro de cada operação:**
  * previsão e score antes da entrada;
  * timestamp do sinal e da ordem;
  * preço esperado e preço efetivo;
  * slippage;
  * funding efetivo;
  * taxas;
  * PnL líquido;
  * drawdown marcado a mercado;
  * resultado por ativo, direção e regime;
  * controles pareados B e D.

## 5. Avaliação final

O `paper/report.py` produz:

* expectativa líquida por trade e por período, com intervalos de confiança robustos;
* Sharpe, profit factor e drawdown;
* comparação com os controles B e D;
* slippage observado contra os 3 bps assumidos;
* concentração do lucro por ativo e por evento;
* diferença entre o previsto e o realizado.

O limite de 50% do lucro vindo de um único ativo é avaliado junto com a concentração de risco e
o drawdown, nunca isoladamente. **A aprovação não pode ser declarada só porque o retorno
acumulado ficou positivo.**

## 6. V2 (em paralelo)

Linhas de investigação para uma nova versão:

* calibração isotônica dentro de cada fold;
* ranking por expectativa líquida estimada;
* universo point-in-time;
* funding histórico;
* spread e slippage observados;
* decomposição do resultado em direção, seleção de ativo e sizing.

A V2 não substitui retroativamente a v1. Ela também não pode usar o mesmo período prospectivo
para ajustes repetidos e depois alegar validação independente.

## 7. Conclusão

Continuar o projeto, congelar a versão atual e iniciar paper trading instrumentado. Não usar
capital relevante até que a expectativa líquida seja demonstrada prospectivamente, com custos
reais e incerteza estatística controlada. O objetivo agora não é melhorar o backtest, e sim
descobrir se o sistema funciona fora dele.
