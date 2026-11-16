# AI Development Analytics

Plataforma de observabilidade do desenvolvimento de software assistido por IA.
Coleta, normaliza e correlaciona dados do **Claude Code**, do **ccusage** e do
**Git** para permitir análise quantitativa e qualitativa das sessões.

> **Estado atual: Etapas 1–10 do MVP completas** — investigação, coleta,
> relatório JSON, persistência em PostgreSQL com sincronização incremental,
> API REST, análise por LLM e dashboard.
>
> A análise por LLM está **desligada por padrão** e nunca foi executada contra
> a API real neste ambiente — não há credencial configurada.

## Princípio central

Dados **observados** e dados **inferidos** nunca se misturam.

```
RAW DATA (transcript JSONL, ccusage, git)
   ↓  coleta — nunca altera a fonte
NORMALIZED DATA (SessionReport → PostgreSQL, este estágio)
   ↓  análise por LLM (Etapa 9, desativada por padrão)
ANALYSIS (tabela ai_analyses — a única com dado inferido)
   ↓
AGGREGATION
```

Três tipos de dado, nunca misturados: **observado** (transcript, ccusage,
git), **inferido** (`ai_analyses`, produzido por LLM) e **declarado**
(`session_surveys`, respondido pelo desenvolvedor). Percepção de qualidade
não está em nenhum transcript — precisa de instrumento.

Todo `SessionReport` carrega `source.transcript_sha256`, permitindo o caminho
inverso: `métrica → sessão → evento → transcript original`.

## Uso

```bash
cp .env.example .env          # ajuste POSTGRES_PASSWORD
docker compose up -d postgres

cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/alembic upgrade head
```

Coleta sem banco:

```bash
./.venv/bin/python -m app.cli.main doctor          # inspeciona o ambiente
./.venv/bin/python -m app.cli.main projects        # lista projetos
./.venv/bin/python -m app.cli.main sessions --limit 10
./.venv/bin/python -m app.cli.main report <SESSION_ID> --json --out ../reports
```

Com banco:

```bash
./.venv/bin/python -m app.cli.main sync            # só o que mudou
./.venv/bin/python -m app.cli.main sync --dry-run  # mostra sem gravar
./.venv/bin/python -m app.cli.main sync --full     # reprocessa tudo
./.venv/bin/python -m app.cli.main db-projects
./.venv/bin/python -m app.cli.main db-sessions --limit 20
./.venv/bin/python -m app.cli.main show <SESSION_ID>

# Métricas de pesquisa
./.venv/bin/python -m app.cli.main compare                    # projetos lado a lado
./.venv/bin/python -m app.cli.main baseline --project-id 2    # git antes/depois da adoção
./.venv/bin/python -m app.cli.main survey <SESSION_ID> \
    --quality 4 --productivity 5 --effort 3 --trust 4 --rework
```

API REST:

```bash
./.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000/docs
```

Dashboard:

```bash
cd frontend && npm install && npm run dev    # http://localhost:3000
```

Ou tudo junto: `docker compose up -d` (postgres + backend + frontend).

| Endpoint | Observação |
|---|---|
| `GET /health` | alcance do banco e `ai_analysis_enabled` |
| `GET /projects`, `/projects/{id}` | métricas agregadas por projeto |
| `GET /sessions` | paginado; filtros `project_id`, `status`, `search` |
| `GET /sessions/{id}` | aceita o UUID do Claude Code **ou** o id numérico |
| `GET /sessions/{id}/messages` | `message_type` repetível; conteúdo íntegro, nunca truncado |
| `GET /sessions/{id}/tools` | estatísticas + chamadas paginadas |
| `GET /sessions/{id}/usage` | todas as fontes + `preferred` |
| `GET /sessions/{id}/git` | associações com evidência |
| `GET /sessions/{id}/analysis` | inferido; 404 enquanto não houver análise |
| `POST /sessions/{id}/analyze` | 503 desabilitado/sem credencial, 502 falha do provider |
| `GET /analytics/overview`, `/analytics/projects/{id}` | agregados |

**Nunca some `usage` entre fontes.** ccusage e transcript descrevem os mesmos
tokens e discordam por construção; somar dobra os números, e filtrar por uma só
descarta 28% das sessões neste ambiente. A API já resolve isso: cada sessão
contribui com uma linha e diz qual fonte usou em `usage_source`.

A sincronização é incremental: neste ambiente, 53 transcripts levam **31,4s na
primeira execução e 3,9s na segunda**, com 51 pulados por impressão digital
inalterada.

`doctor` valida as três fontes e mostra o casamento entre elas:

```
== Claude Code ==
  projetos  : 19
  sessoes   : 53
== ccusage ==
  versao    : ccusage 20.0.20
  casadas   : 38
  so no transcript: 15
== git ==
  projetos com repositorio: 5/19
```

## Testes

```bash
cd backend && ./.venv/bin/python -m pytest
```

A suíte roda em SQLite em memória e não precisa de banco externo.

Dois arquivos pulam sozinhos quando o ambiente não os suporta:

- `test_integration_real_environment.py` roda contra os dados reais da máquina.
  Um dos testes falha de propósito se o Claude Code introduzir um tipo de evento
  novo — deriva de schema deve ser barulhenta.
- `test_postgres_integration.py` cobre o que só o PostgreSQL responde: a
  migration real via `alembic check`, e a recusa de bytes NUL.

## Estrutura

```
backend/app/
  collectors/claude/    discovery.py, parser.py   ← componente mais crítico
  collectors/ccusage/   collector.py
  collectors/git/       collector.py
  schemas/observed.py   dados observados (Pydantic)
  models/entities.py    tabelas (SQLAlchemy)
  repositories/         persistência idempotente + sanitize.py
  services/             aggregator.py, report.py, sync.py, analytics.py
  analyzers/            schema.py, signals.py, digest.py, redaction.py, service.py
  llm/                  base.py, anthropic_provider.py, registry.py
  api/                  deps.py, routers/{projects,sessions,analytics}.py
  main.py               aplicação FastAPI
  cli/main.py
backend/alembic/        migrations
frontend/
  app/                  visão geral, projetos, projeto, sessões, detalhe da sessão
  components/           OverviewPanels (compartilhados), ColumnChart, CategoryBars
  lib/                  cliente tipado da API e formatação
fixtures/claude/        transcripts sintéticos (inclui corrompidos)
docs/adr/               decisões arquiteturais, com as evidências
reports/                saída JSON por sessão
```

## Decisões arquiteturais

Leia antes de mexer nos collectors — cada uma registra o que foi **observado**
no ambiente, não suposto:

- [ADR 0001 — Formato real do transcript](docs/adr/0001-formato-do-transcript-do-claude-code.md)
- [ADR 0002 — ccusage como fonte de custo, e seus limites](docs/adr/0002-ccusage-como-fonte-de-custo.md)
- [ADR 0003 — Correlação entre sessões e commits](docs/adr/0003-correlacao-claude-git.md)
- [ADR 0004 — Persistência e sincronização incremental](docs/adr/0004-persistencia-e-sincronizacao.md)
- [ADR 0005 — API REST](docs/adr/0005-api-rest.md)
- [ADR 0006 — Análise por LLM](docs/adr/0006-analise-por-llm.md)
- [ADR 0007 — Dashboard](docs/adr/0007-dashboard.md)
- [ADR 0008 — Métricas de pesquisa](docs/adr/0008-metricas-de-pesquisa.md)

## Limitações conhecidas

| Limitação | Impacto | Mitigação |
|---|---|---|
| Arquivos editados via `Bash` (heredoc, `sed`) não aparecem no transcript | sessões assim reportam lista de arquivos vazia, e a correlação com git não encontra sobreposição | correlação temporal ainda é reportada, sem a flag `likely_related` |
| ccusage deduplica sessões retomadas | custo por sessão subestimado em ~1 de 38 sessões neste ambiente | `usage.source` explícito + fallback para o transcript |
| Sessões escrevem arquivos de trabalho em `/tmp` | numa sessão real, 18 de 41 arquivos tocados eram scratch | marcados com `is_within_project: false` e contados em `files_outside_project` |
| `cost-state` do transcript pode vir zerado após checkpoint de rate-limit | custo indisponível por essa via | `cost_usd` fica `null`; o ccusage supre |
| `status` da sessão é heurístico | o Claude Code não grava "fim de sessão" | documentado em `_determine_status`; `ACTIVE` = transcript tocado nos últimos 30min |
| Duração wall-clock ≠ tempo de trabalho | sessões que atravessam a noite mostram 74h | `active_turn_seconds` (soma de `turn_duration`) é coletado em paralelo |
| Transcripts com bytes NUL não cabem em `text` do PostgreSQL | 1 sessão de 53 falhou na primeira sincronização | NUL removidos na gravação, com aviso na sessão; o arquivo original fica intacto |
| Sessões retomadas dividem consumo com a sessão-mãe | custo por sessão isolada é aproximado | detectável via `usage_message_keys`; agregações por projeto/dia não são afetadas |
| Formato do transcript muda entre versões do Claude Code | versões de novembro de 2026 gravam tipos de evento não mapeados (`api-request`, `session-owner`, entre outros) e o ccusage passou a incluir sessões do Codex; os testes de `test_integration_real_environment.py` acusam isso | os eventos desconhecidos são contados em `unknown_event_types`, sem perda do dado bruto; a versão `v1.0-tcc` foi validada com os transcritos de julho a outubro de 2026 |

## Análise por LLM (opt-in)

Desligada por padrão. Para habilitar, no `.env`:

```bash
AI_ANALYSIS_ENABLED=true
LLM_PROVIDER=anthropic
LLM_API_KEY=...            # ou exporte ANTHROPIC_API_KEY
```

```bash
# Veja exatamente o que sairia da máquina, sem enviar nada:
./.venv/bin/python -m app.cli.main analyze <SESSION_ID> --dry-run --show-digest

./.venv/bin/python -m app.cli.main analyze <SESSION_ID>
./.venv/bin/python -m app.cli.main analyze --all --limit 10
```

O que é enviado é um **digest limitado** (6–16 KB para sessões que acumularam
centenas de milhões de tokens), nunca o transcript. Antes do envio passa por
redação: prefixo do home, e-mails, credenciais em URL e strings com cara de
chave. O caminho relativo ao projeto é preservado, porque é o sinal útil.

`requires_rework` só é aceito com `rework_evidence` concreta — uma resposta que
afirma retrabalho sem justificar é rejeitada e não chega ao banco.

## Dashboard

Seis telas: visão geral, projetos, **indicadores de um projeto**, sessões,
detalhe da sessão e **conversa completa**.

A conversa traz a transcrição integral — prompts e respostas, sem cortes —
com recortes por tipo e as chamadas de ferramenta agrupadas.

A página de um projeto traz os mesmos indicadores da visão geral recortados
para ele — os painéis são literalmente os mesmos componentes, então os dois
recortes não podem divergir — mais a distribuição de ferramentas e as sessões
recentes.

Três regras da coleta sobrevivem à interface, porque desfazê-las na última
camada anularia todo o resto:

- **Observado e inferido são visualmente separados.** A única seção inferida é
  um cartão isolado, rotulado, com o provedor e o modelo que a produziram.
- **Nenhum número aparece sem a fonte.** `usage_source` é coluna na listagem e
  na tabela por fonte do detalhe.
- **Custo indeterminado é `n/d`, nunca `US$ 0,00`.**
- **Filtro de período** em todas as telas (`?since=&until=`), escopando também
  cobertura e atribuição. A linha de base é a exceção: são janelas fixas em
  torno da adoção.
- **Cada campo carrega sua definição** num `ⓘ`, a partir de uma fonte única
  (`frontend/lib/metrics.ts`) — o mesmo texto em todas as telas, pronto para citar.

O tile grande do detalhe é o *trabalho* (turnos ativos), com a duração de parede
ao lado em cinza — numa sessão real isso é 2h57m contra 118h16m.

## Usando em pesquisa

Três painéis existem para impedir que o dashboard leia como mais certo do que é:

- **Atribuição** — teto da parcela de linhas commitadas que passou por edição
  rastreada da IA. Medido aqui: 6,3% num projeto, 42,8% noutro. Sem esse número,
  "linhas por hora" produz sobre-atribuição grande.
- **Cobertura** — 53 sessões sustentam afirmações sobre esforço, mas só 10 têm
  commit associado. As duas bases aparecem lado a lado.
- **Ameaças à validade** — versões do Claude Code no período, sessões que
  editaram via shell, sessões retomadas, linhas corrompidas.

A **linha de base** compara a atividade git em duas janelas do mesmo repositório,
separadas pela adoção da IA. É descritiva, nunca causal: equipe, fase e escopo
mudam entre as janelas e nada disso é controlado.

Cuidado ao escrever consultas próprias: juntar sessões e commits numa query só
faz o `JOIN` multiplicar as sessões — uma tentativa reportou 1.169h para um
projeto que tem 28,5. Separe em CTEs.

## Citação e uso no TCC

Esta ferramenta foi desenvolvida e usada como instrumento de coleta no Trabalho de
Graduação "Esforço, Produtividade e Percepção de Qualidade no Desenvolvimento de
Software com um Agente de Codificação por IA: um Estudo de Caso Longitudinal"
(Sistemas de Informação, UPE Campus Caruaru, 2026). A versão usada no estudo é a
tag `v1.0-tcc`, arquivada no Zenodo com DOI
[10.5281/zenodo.24278833](https://doi.org/10.5281/zenodo.24278833). Os scripts de análise, os resultados agregados e os instrumentos
estão no pacote de replicação do trabalho:
<https://github.com/devwander/tcc-agente-codificacao-replicacao>
(DOI [10.5281/zenodo.24278835](https://doi.org/10.5281/zenodo.24278835)).

Os dados de sessão do estudo não são publicados: pertencem a um produto clínico
sujeito à LGPD. Para citar a ferramenta, use o arquivo `CITATION.cff`.

## Licença

Código sob a licença MIT (arquivo `LICENSE`).

## Preservação do dado bruto

O Claude Code remove os próprios transcripts com o tempo — numa auditoria, **8
de 53 sessões** já apontavam para arquivos inexistentes, incluindo uma de 6,2 MB
com 41 prompts. A sincronização copia cada transcript para `archive/transcripts/`
na primeira leitura, com verificação por SHA-256.

Custa cerca de **120 MB por 50 sessões**. O diretório fica fora do git (dados
privados de sessão) — faça backup dele à parte, é a matéria-prima da pesquisa.
Desligue com `ARCHIVE_TRANSCRIPTS=false` se preferir.

O painel de ameaças à validade mostra quantas sessões perderam a origem e não
podem mais ser auditadas.

## Privacidade

Os dados são locais e nada é enviado para fora sem configuração explícita.
A coleta objetiva funciona integralmente com a análise desligada.
