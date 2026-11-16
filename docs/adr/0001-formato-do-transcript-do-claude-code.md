# ADR 0001 - Formato real do transcript do Claude Code

**Status:** aceito
**Data:** 2026-08-28
**Ambiente inspecionado:** Claude Code 2.1.250, Linux, 19 projetos / 53 sessões / 39.300 linhas JSONL

## Contexto

O briefing proíbe assumir formatos de arquivo. Este ADR registra o que foi
**observado** no ambiente, não o que foi suposto.

## Localização

`~/.claude/projects/<chave-do-projeto>/<session-uuid>.jsonl`

Um arquivo por sessão. Verificado em 53/53 arquivos: o *stem* do nome do arquivo
sempre aparece no campo `sessionId` dos registros, e nenhum arquivo contém mais
de um `sessionId`. Portanto **nome do arquivo = ID da sessão**.

## A chave do diretório é uma codificação com perda

`-home-user-Dev-my-project-name`

O Claude Code substitui `/`, `-` **e espaços** por `-`. A string acima é
ambígua entre `/home/user/Dev/my-project-name` e
`/home/user/Dev/my/project/name`. Decodificar `-` → `/`
falhou em 9 dos 19 projetos deste ambiente, e um caso é impossível de recuperar:
`/home/user/Downloads/Projeto V2 atualizado` contém espaços.

**Decisão:** a chave do diretório é tratada como identificador opaco. O caminho
real do projeto vem do campo `cwd` presente nos registros. Isso resolveu 100%
dos projetos com transcript não-vazio.

## Tipos de evento observados (19)

| Tipo | Ocorrências | Uso |
|---|---:|---|
| `assistant` | 12.571 | mensagens, tool_use, thinking, usage |
| `user` | 8.289 | prompts humanos **e** resultados de ferramenta |
| `attachment` | 7.074 | lembretes/contexto injetado — ignorado |
| `last-prompt` | 1.873 | último prompt da sessão |
| `permission-mode`, `mode` | 3.681 | estado da UI — ignorado |
| `ai-title` | 1.765 | título gerado pelo próprio Claude Code |
| `bridge-session`, `atis-latch` | 1.551 | interno — ignorado |
| `system` | 603 | `turn_duration`, `compact_boundary`, etc. |
| `file-history-delta` | 592 | arquivo modificado (rastreado pelo Claude Code) |
| `agent-name` | 492 | subagentes |
| `file-history-snapshot` | 465 | snapshot — ignorado |
| `frame-link`, `queue-operation`, `custom-title` | 287 | diversos |
| `cost-state` | 21 | **custo e tokens por modelo, escritos pelo próprio Claude Code** |
| `artifact-*` | 35 | interno — ignorado |

Tipos fora dessa lista são contados em `source.unknown_event_types` e viram
`warning`. O teste de integração falha quando aparece um tipo novo — a deriva de
schema deve ser barulhenta, não silenciosa.

## `type: "user"` não significa "um humano falou"

Dos 8.289 registros `user`, apenas **427** trazem `origin: {"kind": "human"}`.
Os demais são:

- resultados de ferramenta (`content[].type == "tool_result"`) — 7.712 blocos;
- o resumo de compactação (`isCompactSummary: true`);
- notas injetadas (`isMeta: true`, `isVisibleInTranscriptOnly: true`);
- notificações (`origin.kind == "task-notification"`).

Contar tudo isso como prompt inflaria toda métrica de interação humana. O parser
separa em `HUMAN_PROMPT`, `COMPACT_SUMMARY`, `INJECTED_USER` e `TOOL_RESULT`, e
expõe `confirmed_human_prompt_count` (com `origin.kind == "human"`) separado de
`human_prompt_count` — versões antigas do Claude Code não escreviam `origin`.

## Blocos de conteúdo

`assistant`: `tool_use` (7.714), `thinking` (3.012), `text` (1.847).
`user`: `tool_result` (7.712), `text` (134), `image` (58), `document` (1).

## `toolUseResult` — dados estruturados de cada ferramenta

Além do `tool_result` textual, o registro `user` carrega `toolUseResult` com o
resultado estruturado. As formas observadas:

- Bash: `stdout`, `stderr`, `interrupted`, `isImage`, `noOutputExpected`
- Edit: `filePath`, `oldString`, `newString`, `structuredPatch`, `userModified`
- Write: `filePath`, `content`, `structuredPatch`, `type` (`create`/`update`)
- Read: `file`, `type`

`structuredPatch` fornece contagem real de linhas `+`/`-` por arquivo, sem
precisar do git.

## Um registro `assistant` por bloco de conteúdo — e o `usage` se repete

Uma mensagem da API com 6 blocos vira **6 registros `assistant`**, e cada um
carrega o objeto `usage` **completo da mensagem inteira**. Somar `usage` por
registro conta o mesmo consumo várias vezes.

Medido em `a753069a-…`: 361 registros `assistant` para 149 mensagens de API
reais. Soma ingênua = 69.557.510 tokens; soma deduplicada por
`(message.id, requestId)` = **29.822.171**, idêntico ao que o ccusage reporta.
Fator de inflação: 2,33x.

O parser deduplica por `(message.id, requestId)` e expõe
`assistant_message_count` (mensagens de API) separado de
`assistant_record_count` (linhas do transcript). Verificado contra o ccusage em
todas as sessões deste ambiente que não compartilham mensagens com outra sessão.

## O `cwd` mais frequente não é o projeto

Uma sessão se move entre subdiretórios. Em `f50b5adf-…` os `cwd` registrados são
`.../projeto-x/backend` (868), `.../projeto-x/frontend` (832), `.../projeto-x` (796),
`.../projeto-x/frontend/src` (238) e `.../projeto-x/.claude` (137). Usar o mais frequente
nomeava o projeto como **"backend"**.

O diretório mais profundo que contém todos os `cwd` dá a resposta certa
(`/home/user/work/projeto-x`). Quando o git collector encontra a raiz do
repositório, ela prevalece. O campo `project_path_source` registra qual das três
regras foi usada, e `primary_cwd` guarda a observação bruta.

Guarda: se o ancestral comum tiver menos de 4 componentes (`/`, `/home`,
`/home/user`), o `cwd` mais frequente é usado — o sistema não declara que uma
sessão pertence a `/`.

## `cost-state` zerado é contador reiniciado, não custo zero

Após um checkpoint de rate-limit o Claude Code grava um `cost-state` com
`totalCostUSD: 0`, `totalAPIDuration: 0` e `modelUsage: {}`. Em `f50b5adf-…` o
relatório chegou a exibir **US$ 0,0000** para uma sessão que custou US$ 247,08.

Regra: o `cost-state` só é aceito quando `modelUsage` não está vazio. Caso
contrário `cost_usd` fica `null` com a justificativa em `usage.notes` — nunca um
zero inventado.

## Arquivos de trabalho fora da árvore do projeto

Sessões escrevem screenshots, HTML temporário e assets em
`/tmp/claude-.../scratchpad/`. Em `f50b5adf-…` isso era **18 dos 41 arquivos**
tocados. Contá-los como alteração de código distorce toda métrica de arquivo.

São marcados com `is_within_project: false` e contados em
`files_outside_project`, nunca descartados — continuam sendo dado observado.

## Arquivos corrompidos existem de verdade

`96f5be24-….jsonl` linha 7536 contém 1.667 bytes NUL. O parser trata linhas
inválidas como `unparseable_lines` e segue.

## Consequências

- O parser preserva o transcript original; o relatório guarda caminho, tamanho e
  SHA-256 para auditoria (`métrica → sessão → evento → transcript`).
- Arquivos alterados via `Bash` (heredoc, `sed`) **não** aparecem em
  `file-history-delta` nem em `toolUseResult`. Sessões que editam por Bash
  produzem lista de arquivos vazia. Essa é a principal lacuna da coleta e é o
  motivo pelo qual a correlação com o git existe.
