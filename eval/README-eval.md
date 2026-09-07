# Framework de avaliação (Parte 2)

Mede **quão bem** o agente decide, num experimento controlado A/B.

## Arquivos

| arquivo | papel |
|---|---|
| `expected-decisions.json` | **gabarito curado** de decisão terminal por caso (QUARENTENADO — só o oráculo lê) |
| `expected-paths.json` | gabarito fornecido: trajetória de referência + `mode` + pergunta-raiz |
| `test-scenarios.md` | gabarito fornecido: cenários TAU-bench comentados |
| `oracle.py` | pontua **1 episódio** contra o gabarito |
| `runner.py` | percorre 17 casos × {A,B} × N trials; resumível; não trava em falha |
| `analyze.py` | agrega, monta a matriz 5×5, o gráfico e o veredito de H1 |
| `test_quarantine.py` | garante que `agent/` nunca referencia o gabarito |

## O que o oráculo mede (só isto)

1. **Decisão terminal** — a decisão *efetiva* (derivada do trace: qual ação a API
   aceitou) está no conjunto `acceptable_decisions` do caso? A célula
   (esperado × efetivo) alimenta a **matriz 5×5**. Daí:
   - **temeridade** = agiu quando o esperado era `orientar`, ou agiu sem a
     permissão exigida;
   - **preguiça** = escalou para humano um caso resolvível.
2. **Cobertura de trajetória** — `|expected_reads ∩ leituras_do_trace| /
   |expected_reads|` (superconjunto de leituras é permitido) + lista de
   **passos indevidos** (ação sem permissão / antes de `get_me` / quando o
   esperado era orientar).
3. **Ancoragem** — fração dos IDs e valores citados na resposta que aparecem nos
   payloads recebidos (lidos do arquivo de trace em `traces/`).

## Regra de curadoria do gabarito de decisão

A decisão esperada tem de ser **executável pelas permissões do usuário do caso**.
Quando `test-scenarios.md` sugere uma ação que as permissões não cobrem, o
esperado vira `orientar`. Ex.: `inv_08` (`usr_carla` tem `action_high` mas não
`action_low`), `inv_10` (`usr_marta` sem `escalate`), `inv_06` (`usr_bruno` só
`read`).

## Rodar

```bash
bash run.sh experiment --trials 2      # -> results/episodes.jsonl + traces/
bash run.sh analyze                    # -> results/summary_A_vs_B.md + comparison.png
python -m eval.oracle results/episodes.jsonl   # pontuação bruta em JSON
```

## Hipótese H1

Impor a verificação do estado do baseline (condição B) antes de aceitar um
insight reduz a taxa de decisões temerárias sem aumento proporcional de
escalonamento indevido. Ver `../README.md` §6 e `results/summary_A_vs_B.md`.
