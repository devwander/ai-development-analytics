# ADR 0005 - API REST

**Status:** aceito
**Data:** 2026-08-28
**Stack:** FastAPI 0.141, Pydantic 2

## A regra que a API precisa fazer valer

`usages` guarda um total por (sessão, fonte), e as fontes descrevem **os mesmos
tokens** discordando por construção (ADR 0002). Duas armadilhas:

- **Somar as fontes** dobra os números.
- **Filtrar por uma fonte** descarta dados: neste ambiente 15 das 53 sessões não
  têm linha do ccusage (28%).

Solução: cada sessão contribui com **exatamente uma linha**, da fonte preferida
— ccusage quando existe, transcript caso contrário. A escolha viaja junto no
campo `usage_source`, então nenhum número da API é opaco quanto à origem.

Implementado com função de janela (`row_number() OVER (PARTITION BY session_id)`)
e não com `DISTINCT ON`, para a mesma query rodar em SQLite nos testes.

O endpoint `/sessions/{id}/usage` devolve **todas** as fontes em `totals` mais o
campo `preferred`. Quem precisa de um número usa `preferred`; quem quer auditar
compara as fontes. `totals` nunca deve ser somado, e a docstring do endpoint diz
isso.

Há um teste específico para essa regressão
(`test_overview_counts_each_session_once_across_sources`): uma sessão com as
duas fontes deve contribuir com 500.000 tokens no agregado, não com a soma.

## Observado vs inferido na superfície HTTP

Todo endpoint devolve dado observado, com uma exceção:
`/sessions/{id}/analysis`, cujo response model tem `inferred: Literal[True]` e
`confidence`. Enquanto o analisador não existir (Etapa 9) ele responde 404.

`POST /sessions/{id}/analyze` responde **503** quando `AI_ANALYSIS_ENABLED=false`
e **501** quando habilitado mas não implementado. Nunca devolve análise
fabricada. A coleta objetiva não depende dele.

## Identificação de sessão

`/sessions/{id}` aceita **o UUID do Claude Code ou o id numérico do banco**. Quem
vem de um transcript ou da CLI conhece o UUID; quem vem de uma listagem tem o
id. Recusar um dos dois seria gratuito.

## Paginação

`/sessions`, `/sessions/{id}/messages` e `/sessions/{id}/tools` são paginados
(`limit` <= 500). Não é enfeite: uma sessão real tem 2.396 mensagens e 751
chamadas de ferramenta.

`/sessions/{id}/messages?message_type=human_prompt` é o filtro que devolve o que
o desenvolvedor realmente digitou, separando de tool results, resumos de
compactação e notas injetadas (ADR 0001).

## Escrita

A API é somente leitura sobre o banco. A coleta roda pela CLI
(`python -m app.cli.main sync`), então o processo da API nunca toca os dados
locais do Claude Code. No contêiner, `~/.claude` é montado como somente leitura.

## O que não existe

Nenhum endpoint expõe autoria de commit. `/sessions/{id}/git` devolve
`likely_related` com as razões que o produziram, e um teste garante que a chave
`caused_by_ai` não aparece na resposta.
