# ADR 0003 - Correlação entre sessões e commits

**Status:** aceito
**Data:** 2026-08-28

## Princípio

"Claude alterou um arquivo" e "houve um commit" são eventos distintos. O sistema
registra os dois separadamente e calcula uma **associação**, nunca uma
causalidade.

## Regra implementada

Janela de busca: `[início − 30min, fim + 120min]`, com `git log --all` (o
trabalho de uma sessão frequentemente vive numa branch que já não está em HEAD).

Um commit recebe `likely_related: true` somente quando:

- compartilha **pelo menos um arquivo** com o que o Claude modificou na sessão
  (leituras não contam), **e**
- ocorre dentro da janela da sessão ou depois do fim dela.

Há uma segunda via de evidência, mais forte: quando a **mensagem do commit cita
o ID da sessão**. O próprio Claude Code faz isso nos seus commits de checkpoint
(`WIP: Claude Code rate-limit checkpoint (f50b5adf)`). Nesse caso a associação
não depende de sobreposição de arquivos. O ID curto de outra sessão na mensagem
não conta.

Proximidade temporal isolada **não** basta — é reportada em `reasons` sem a
flag. Todo `GitCorrelation` carrega a evidência textual que o produziu.

O campo `caused_by_ai` não existe e não deve ser criado.

## Detalhe de implementação que causou bug real

Com `git log --pretty=format:… --numstat`, o git imprime o cabeçalho formatado e
**depois** as linhas de numstat. Colocar o separador de registro no fim do
formato faz o numstat de cada commit ser atribuído ao commit seguinte — na
primeira execução isso produziu "1 commit, 0 arquivos" onde havia 25 commits.
O separador (`\x1e`) precisa **preceder** o cabeçalho. Coberto por
`test_numstat_is_attached_to_the_right_commit`.

Separadores ASCII (`\x1f` entre campos, `\x1e` entre registros) são usados porque
mensagens de commit reais contêm `|`, tabs e quebras de linha.

## Redação das evidências

Um commit anterior ao início da sessão dizia "X min before the session ended",
tecnicamente verdadeiro e enganoso. Commits antes do início agora reportam
"before the session started".
