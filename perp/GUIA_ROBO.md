# Guia do robô: rodar no seu notebook (paper ou live)

O robô roda a versão congelada **v1** (veja `VEREDITO.md`): o modelo e as regras não mudam. Você
escolhe pelo `.env`:

* **o modo:** `paper` (simulação com preços reais) ou `live` (envia ordens à Bybit);
* **o tipo de execução:** perfil `oficial`, idêntico ao backtest, ou perfil `maker`.

> **O que o veredito exige:** só o **paper no perfil oficial** conta para o protocolo de validação
> (6 meses e 200 trades). O perfil `maker` é uma variante de execução registrada em outra pasta.
> Live com dinheiro real não está aprovado. Se for testar live, use a conta **Demo** da Bybit
> ou um capital mínimo que você aceite perder.

## 1. Instalação (uma vez)

**Windows**

1. Instale o Python 3.11 ou mais novo em python.org, marcando "Add Python to PATH".
2. Baixe o repositório e abra a pasta `perp`.
3. Dê dois cliques em `instalar.bat`. Ele cria o ambiente, instala as dependências e copia
   `.env.example` para `.env`.

**Linux ou macOS**

```bash
cd perp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## 2. Telegram

1. No Telegram, fale com **@BotFather**, envie `/newbot` e copie o token.
2. Abra uma conversa com o seu bot novo e mande qualquer mensagem.
3. Fale com **@userinfobot** para descobrir o seu chat id.
4. No `.env`, preencha `TELEGRAM_BOT_TOKEN=...` e `TELEGRAM_CHAT_ID=...`.

O robô envia:

* início e parada;
* cada entrada (preço, maker ou taker, TP, SL, risco);
* cada saída (motivo, PnL, R, taxas, funding);
* erros (no máximo 1 a cada 15 min por tipo);
* aviso de kill-switch;
* resumo diário às 00:05 UTC;
* retreino mensal.

## 3. Configurar o `.env`

**Paper oficial (recomendado agora):**

```
MODE=paper
CAPITAL_USDT=1000
ENTRY_MODE=taker
TP_ORDER=market
EXIT_MODE=taker
```

**Live na conta Demo da Bybit** (preços reais, dinheiro fictício):

1. Em bybit.com, entre em Demo Trading e crie uma chave de API dentro do modo demo.
2. Dê as permissões **Contratos → Ordens e Posições**. **Nunca** dê permissão de saque.
3. Preencha o `.env`:

```
MODE=live
BYBIT_ENV=demo
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
```

**Live com dinheiro real:** use `BYBIT_ENV=mainnet` e também `LIVE_CONFIRM=EU_ENTENDO_O_RISCO`.
Sem essa linha o robô se recusa a operar. Use uma subconta com só o capital do teste e
`CAPITAL_USDT` baixo.

### Configurações de conta na Bybit (live)

* O robô coloca a conta em **one-way mode** e ajusta a alavancagem por par (`LEVERAGE=5`). Ele
  avisa no Telegram se não conseguir.
* **Margem:** use **cross** (padrão da conta unificada). O tamanho de cada posição é definido pelo
  risco de 0,5% por trade, não pela alavancagem.
* **Capital mínimo:** cada trade usa um notional de cerca de 25 a 30% do capital. Com menos de
  cerca de 300 a 500 USDT, o BTC fica abaixo do mínimo da Bybit (0,001 BTC). O `--check` mostra
  isso para cada par.

## 4. Testar antes de ligar

```
.venv\Scripts\python.exe run_bot.py --check      (Windows)
.venv/bin/python run_bot.py --check              (Linux/macOS)
```

O teste confere:

* o `.env`;
* a conexão com a Binance (sinal) e com a Bybit (preços);
* a conta Bybit (no modo live): saldo e posições;
* o tamanho de cada par em relação ao mínimo;
* o Telegram (envia uma mensagem de teste).

**Nenhuma ordem é enviada.**

## 5. Ligar

* **Windows:** dê dois cliques em `iniciar_robo.bat`. Se o robô cair, ele reinicia sozinho.
* **Linux ou macOS:** rode `./iniciar_robo.sh`.

Deixe a janela aberta. O robô verifica tudo a cada 15 s e decide as entradas 1 minuto após o
fechamento de cada vela de 2h (00:00, 02:00, ... UTC, ou seja, 21:00, 23:00, ... no horário de
Brasília).

### O notebook precisa ficar acordado

Em *Energia e suspensão*, coloque "Nunca suspender" com o carregador ligado e desative a suspensão
ao fechar a tampa.

**Se o notebook dormir:**

* **No live:** as posições continuam protegidas pelo stop na Bybit. A saída por tempo e as novas
  entradas atrasam, e velas com mais de 15 min de atraso são puladas.
* **No paper:** a simulação congela e recupera o que aconteceu quando o robô volta (ela reprocessa
  as velas de 1 min do período).

Velas perdidas ficam registradas como "perdidas" e prejudicam a comparação com o backtest. Para
6 meses de protocolo, um PC sempre ligado ou uma VPS barata é o ideal.

## 6. Comandos úteis

| Comando | O que faz |
|---|---|
| `run_bot.py --status` | Posições abertas e equity registradas |
| `run_bot.py --resume` | Retoma as entradas após o kill-switch (`MAX_DRAWDOWN_PCT`, padrão 15%) |
| `run_bot.py --report` | Relatório do protocolo (expectativa, IC robusto, controles B e D, custos, concentração) |

## 7. Execução: oficial × maker

| | Oficial (= backtest) | Maker (variante) |
|---|---|---|
| Entrada | A mercado, 1 min após o fechamento da vela | PostOnly no topo do livro, reposicionada a cada `REPRICE_SEC` enquanto o preço não fugir mais que `MAX_CHASE_BPS`. Após `MAKER_TIMEOUT_SEC`, completa a mercado ou desiste (`MAKER_FALLBACK`) |
| Take profit | Gatilho a mercado | Limite reduce-only parada no preço do TP (maker) |
| Stop loss | Stop-market | Stop-market (sempre) |
| Saída por tempo (48 velas) | A mercado | PostOnly com prazo, depois a mercado |
| Taxa | 0,055% por lado | 0,02% nas partes executadas como maker |

* **Por que o maker não é o protocolo oficial:** a ordem maker muda o preço, o momento e às vezes
  a própria existência do trade. Ela costuma ser executada quando o preço vai contra você
  (seleção adversa) e falha quando o preço dispara a favor.
* **Como comparar:** rode as duas variantes em paralelo, em duas pastas com dois `.env`
  (`python run_bot.py --env .env.maker`), e compare com `--report`.
* **Contagem para a validação:** a variante maker só pode ser avaliada como uma **nova versão de
  execução**, num período próprio.

## 8. Arquivos gerados (`runtime/<modo>_<perfil>/`)

| Arquivo | Conteúdo |
|---|---|
| `signals.csv` | Todos os sinais de todas as velas, com a decisão e o motivo |
| `trades.csv` | Cada trade: preço esperado e executado, fração maker, tempo até a execução, slippage, taxas, funding, PnL e R |
| `equity.csv` | Equity realizada e marcada a mercado a cada vela |
| `events.csv` | Cada ordem colocada, cancelada ou executada, mais erros |
| `missed.csv` | Entradas maker não executadas |
| `state.json` | Estado do robô (não edite com o robô ligado) |

O robô se recusa a usar uma pasta criada em outro modo ou perfil, ou criada antes de uma mudança
nos arquivos congelados. Isso evita misturar resultados.

## 9. Limitações conhecidas

* **APIs reais não testadas:** as chamadas à Bybit e à Binance não puderam ser testadas no ambiente
  onde o código foi escrito. Os testes offline (`tests/`) cobrem a lógica completa com mercado
  simulado e o formato das respostas da API. **Comece pelo `--check` e por alguns dias na conta Demo.**
* **Funding no live:** é lido das movimentações de saldo do tipo SETTLEMENT da Bybit. Confira os
  primeiros trades contra o histórico da corretora.
* **Paper:** o slippage das saídas por gatilho é o assumido (3 bps). A ordem maker simulada só é
  executada se o preço negociar *além* do limite (estimativa conservadora da fila).
