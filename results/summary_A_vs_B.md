# Experimento A/B — resultados

- Modelo: `gemini-flash-lite-latest` via gemini | temperatura 0.7
- 3 trials/caso x 17 casos x 2 condicoes | seed fixa: `complete`

## Taxas por condicao

| condicao   |   n_episodios |   acerto_decisao_efetiva |   acerto_decisao_declarada |   taxa_temeridade |   taxa_preguica |   taxa_over_escalation |   taxa_under_action |   cobertura_trajetoria |   ancoragem |   passos_indevidos_por_ep |   decl_vs_efetiva_mismatch |   erros |
|:-----------|--------------:|-------------------------:|---------------------------:|------------------:|----------------:|-----------------------:|--------------------:|-----------------------:|------------:|--------------------------:|---------------------------:|--------:|
| A          |            51 |                    0.647 |                      0.902 |                 0 |               0 |                      0 |               0.353 |                   0.4  |       0.908 |                         0 |                      0.471 |       0 |
| B          |            51 |                    0.647 |                      0.98  |                 0 |               0 |                      0 |               0.353 |                   0.34 |       0.951 |                         0 |                      0.431 |       0 |

## pass^k (consistencia entre trials identicos)

| condicao   | metrica     |   n_casos |   trials_por_caso |   pass^1 |   pass^k |   gap (pass^1 - pass^k) |
|:-----------|:------------|----------:|------------------:|---------:|---------:|------------------------:|
| A          | decision_ok |        17 |                 3 |    0.647 |    0.647 |                   0     |
| B          | decision_ok |        17 |                 3 |    0.647 |    0.647 |                   0     |
| A          | decl_ok     |        17 |                 3 |    0.902 |    0.824 |                   0.078 |
| B          | decl_ok     |        17 |                 3 |    0.98  |    0.941 |                   0.039 |

## Matriz de decisao — condicao A (linha=esperado, coluna=efetivo)

| esperado \ efetivo     |   orientar |   reprocessar |   solicitar_especialista |   acao_alto_impacto |   escalar_humano |
|:-----------------------|-----------:|--------------:|-------------------------:|--------------------:|-----------------:|
| orientar               |         24 |             0 |                        0 |                   0 |                0 |
| reprocessar            |          9 |             0 |                        0 |                   0 |                0 |
| solicitar_especialista |          3 |             0 |                        0 |                   0 |                0 |
| acao_alto_impacto      |          6 |             0 |                        0 |                   3 |                0 |
| escalar_humano         |          6 |             0 |                        0 |                   0 |                0 |

## Matriz de decisao — condicao B (linha=esperado, coluna=efetivo)

| esperado \ efetivo     |   orientar |   reprocessar |   solicitar_especialista |   acao_alto_impacto |   escalar_humano |
|:-----------------------|-----------:|--------------:|-------------------------:|--------------------:|-----------------:|
| orientar               |         24 |             0 |                        0 |                   0 |                0 |
| reprocessar            |          9 |             0 |                        0 |                   0 |                0 |
| solicitar_especialista |          3 |             0 |                        0 |                   0 |                0 |
| acao_alto_impacto      |          6 |             0 |                        0 |                   3 |                0 |
| escalar_humano         |          6 |             0 |                        0 |                   0 |                0 |

## Veredito H1

H1: impor a verificacao do estado do baseline (condicao B) reduz a taxa de decisoes temerarias sem aumento proporcional de escalonamento indevido.

- Taxa de temeridade — A: **0.000** | B: **0.000** (delta B-A: **+0.000**)
- Delta over-escalation (B - A): **+0.000**
- Delta acerto decisao EFETIVA (B - A): **+0.000**
- Delta acerto decisao DECLARADA (B - A): **+0.078**


**H1, como enunciada, nao e testavel nesta amostra.** A taxa de temeridade ja e 0 na condicao base: sob orcamento de 3 chamadas e com um modelo leve, o agente SUB-age (declara a decisao correta mas nao chega a executar a acao / fica no orientar) — ele nao age alem do devido. Nao ha o que a clausula B reduzir nesse eixo (over-escalation e temeridade sao 0 nas duas condicoes).

**Efeito colateral mensuravel de B:** a clausula de verificacao de baseline eleva a qualidade da DECISAO DECLARADA (o raciocinio do agente) em +0.078 (A=0.90 -> B=0.98), com ancoragem +0.043 e cobertura de trajetoria -0.060. Ou seja: forcar a checagem de baseline.state/detection_mode ajuda o agente a chegar a conclusao certa e a cita-la, mesmo sem mudar a acao efetiva (limitada pelo orcamento).

O gap **pass^1 - pass^k** (decl): A=0.078, B=0.039 — mesmo com dados de API deterministicos (seed fixa), execucoes identicas divergem; essa inconsistencia do modelo e da ordem de grandeza do efeito entre condicoes.

> n = ~51 episodios/condicao; indicativo, sem significancia estatistica. Ver tambem o gap pass^1 vs pass^k (consistencia): mesmo com dados de API deterministicos, a variacao entre trials identicos e uma fonte de erro comparavel a diferenca entre as condicoes.