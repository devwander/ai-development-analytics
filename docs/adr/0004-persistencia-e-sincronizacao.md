# ADR 0004 - Persistência e sincronização incremental

**Status:** aceito
**Data:** 2026-08-28
**Ambiente:** PostgreSQL 18 (contêiner), SQLAlchemy 2.0, Alembic 1.19

## Desvio deliberado do modelo de dados do briefing

O §9 previa `GitCommit.session_id` anulável. A implementação usa uma tabela de
associação (`session_commit_links`) porque:

1. o mesmo commit se associa legitimamente a **mais de uma sessão** — uma chave
   estrangeira única forçaria escolher uma arbitrariamente;
2. o §25 exige que a associação carregue **evidência** (arquivos sobrepostos,
   distância temporal, razões). Uma FK não carrega nada disso.

Não existe coluna `caused_by_ai`, e não deve existir. `likely_related` é
associação, nunca autoria.

## `usages`: uma linha por (sessão, fonte, modelo)

`model IS NULL` marca o **total daquela fonte**; as demais linhas são o
breakdown por modelo. `source` é obrigatório e indexado porque as fontes
discordam por construção (ADR 0002) — qualquer agregação precisa filtrar por
ela, senão soma ccusage com transcript.

`cost_usd` é anulável e **nunca recebe 0 como preenchimento**.

## Sincronização incremental

Regra do §23: 100 sessões na primeira execução, só as novas depois. Medido neste
ambiente: **31,4s → 3,9s** (53 transcripts, 51 pulados).

A decisão de reprocessar é barata de propósito: compara tamanho e mtime
primeiro, e só calcula o SHA-256 quando esses divergem. Sobre ~150MB de
transcripts isso troca um re-hash completo por um `stat()` por arquivo.

Um transcript é relido **inteiro**, nunca a partir de um offset em bytes: o
Claude Code reescreve registros finais no lugar (`ai-title`, `last-prompt`,
`cost-state`), então retomar no meio do arquivo perderia atualizações.

Sessões com `status = active` são relidas em toda execução (§24).

Persistência é idempotente: reprocessar substitui as linhas-filhas em vez de
acumular. Uma sessão que cresceu converge, não duplica.

Uma falha isolada não aborta a execução — a sessão entra como `failed`, com a
exceção registrada, e o restante prossegue.

## NUL bytes: o PostgreSQL recusa, o transcript contém

`psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`.

A primeira sincronização real gravou 52 de 53 sessões e falhou em `96f5be24-…`,
o transcript com a região corrompida (ADR 0001). Os NUL vazam da saída de uma
ferramenta para `tool_calls.output_preview`.

Os NUL são removidos **na fronteira de persistência**, nunca no parser: o
arquivo original permanece intacto e a sessão recebe um aviso dizendo quantos
bytes foram removidos de quantos valores. O dado bruto continua auditável no
disco via `session_source_files.transcript_sha256`.

## Fronteira do projeto: dois defeitos corrigidos pelos dados reais

`/home/user/work` é um repositório git **sem nenhum commit e sem
remote**. Ele engolia dois clientes distintos (`fabrics-mocabella` e
`ponto-fixo`) num único "projeto" e escondia o repositório real aninhado em
`fabrics-mocabella-back`.

Duas correções:

1. `find_repository_root` rejeita repositório sem commits (`require_commits`).
2. A raiz do git é resolvida a partir do **cwd predominante**, não do ancestral
   comum. Partir do ancestral sobe *acima* de um repositório aninhado nele.

Resultado: 14 → 17 projetos, com os clientes separados.

## Tipos de coluna portáveis

As entidades usam `JSON` com variante `JSONB` no PostgreSQL, e nenhum `ARRAY`.
Isso deixa a suíte rodar em SQLite em memória (rápida, sem dependência externa)
enquanto a produção usa PostgreSQL. O que só o PostgreSQL responde — a migration
real via `alembic check` e a recusa de NUL — fica em
`tests/test_postgres_integration.py`, que pula sozinho quando não há banco.

## Detalhes operacionais

- A URL do banco vem de `.env` via `app/config.py`. O `alembic.ini` **não**
  contém string de conexão — `alembic/env.py` a injeta.
- `postgres:18+` exige o volume montado em `/var/lib/postgresql`, não em
  `/var/lib/postgresql/data`; a imagem recusa iniciar com a convenção antiga.
- A porta do host é **5434**: 5432 (PostgreSQL local) e 5433 (`outro-postgres`)
  já estavam ocupadas nesta máquina.
- O contêiner do backend monta `~/.claude` e os projetos **como somente
  leitura**. O collector nunca deve poder alterar a fonte.


## Adendo (2026-08-31) - arquivamento dos transcripts

Uma auditoria encontrou **8 de 53 sessões apontando para arquivos que não
existem mais**. Não eram os transcripts vazios: um tinha 6,2 MB e 41 prompts.
O Claude Code remove os próprios transcripts com o tempo, e os números dessas
sessões continuavam na tela com a cadeia de proveniência quebrada atrás deles.

O §18 do briefing exige preservar o dado bruto sempre que possível, então a
sincronização passou a copiar cada transcript para um arquivo local na primeira
leitura. A cópia é **verificada por SHA-256** contra o que o parser calculou: um
arquivo que divergisse em silêncio da origem seria pior que nenhum.

Detalhes que importam:

- Uma cópia idêntica não é reescrita. Sem isso, cada sincronização regravaria
  ~117 MB à toa.
- Falha ao arquivar **não aborta** a coleta: o dado coletado continua válido,
  apenas deixa de ser re-auditável a partir dos bytes.
- O arquivo fica fora do git. São dados privados de sessão, e ~120 MB por 50
  sessões — um repositório é o lugar errado para versioná-los. Faça backup à
  parte.
- `report` cai para a cópia arquivada quando o original sumiu, e distingue as
  duas situações na mensagem: "nunca coletada" lê muito diferente de "origem
  removida".

O painel de ameaças à validade passou a mostrar quantos transcripts estão
arquivados e quantas sessões perderam a origem. As 8 já perdidas são
irrecuperáveis e aparecem como tal — o dashboard não finge que a auditoria está
completa.
