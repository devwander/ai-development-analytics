# ADR 0008 - Métricas de pesquisa: esforço, produtividade e percepção

**Status:** aceito
**Data:** 2026-08-31
**Motivação:** uso da ferramenta em uma monografia sobre esforço, produtividade
e percepção de qualidade no desenvolvimento assistido por IA.

## Três tipos de dado, não dois

Até aqui o sistema separava **observado** de **inferido**. A pergunta de
percepção obriga um terceiro: **declarado**.

| Tipo | Origem | Onde mora |
|---|---|---|
| Observado | transcript, ccusage, git | quase todas as tabelas |
| Inferido | LLM | `ai_analyses` |
| **Declarado** | o desenvolvedor responde | **`session_surveys`** |

Percepção de qualidade não está em nenhum transcript e nenhum modelo pode
deduzi-la — é o que a pessoa diz. Guardá-la em tabela própria mantém a
propriedade que atravessa o projeto: qualquer número sabe dizer de que tipo é.

Respostas Likert vão de 1 a 5, e `NULL` significa **não respondida**, que é
diferente de um 3 neutro.

## Atribuição: o número que precede todos os outros

Medido nos dados reais, "linhas por hora" no maior projeto dá 3.025 — absurdo.
A causa é dupla: um commit contém tudo que estava staged, e a associação exige
apenas *um* arquivo em comum.

A razão de atribuição corrige a leitura:

```
linhas do structuredPatch (o que a IA comprovadamente editou)
────────────────────────────────────────────────────────────
linhas dos commits associados
```

Medido: **6,3%** num projeto, **42,8%** noutro. A diferença entre os dois é a
diferença entre uma sessão que edita por ferramenta e uma que edita por shell.

O valor é um **teto**, e as duas metades são frouxas:

- o numerador ignora arquivos escritos via `Bash` (ADR 0001);
- o denominador conta o commit inteiro, incluindo o digitado à mão.

Portanto ele responde *"no máximo esta parcela das linhas commitadas passou por
uma edição rastreada"*, nunca *"a IA escreveu esta parcela"*. A tela diz isso.

## Cobertura: qual amostra sustenta qual afirmação

Neste conjunto, **53 sessões** sustentam afirmações sobre esforço, mas só
**10** têm commit associado. Publicar as duas lado a lado impede o leitor de
supor que a amostra inteira respalda todos os gráficos.

## Ameaças à validade ficam na tela, não no rodapé

O painel expõe: número de versões do Claude Code no período (**18** no conjunto
global — a ferramenta mudou durante o estudo), modelos usados, sessões que
editaram via shell, sessões provavelmente retomadas (ADR 0002), sessões
interrompidas e linhas corrompidas.

Nada disso é falha. São propriedades do conjunto que uma pesquisa precisa
declarar, e escondê-las faria o resto do dashboard parecer mais certo do que é.

## Formato do esforço, não só a soma

Uma soma não distingue uma maratona de quarenta sessões curtas. O painel reporta
mediana, maior sessão, prompts por sessão e **concentração** — a parcela do tempo
no quinto mais longo. Num projeto real: 66%.

## Linha de base

Sem janela de comparação, produtividade não tem denominador. `ProjectBaseline`
guarda a atividade git em duas janelas do mesmo repositório, separadas pela
adoção da IA.

Duas escolhas:

- **A data de adoção vem do dado.** O padrão é a primeira sessão registrada do
  projeto — foi literalmente quando a ferramenta chegou ali. Pode ser sobrescrita
  quando o projeto já usava IA antes deste conjunto.
- **O resultado é gravado, não recalculado.** Uma linha de base é uma afirmação
  datada com parâmetros; um gráfico que a recalculasse em silêncio seria
  impossível de auditar depois.

A comparação é **descritiva, nunca causal** — tamanho de equipe, fase e escopo
mudam entre as janelas, e nada disso é controlado. Cada linha de base carrega
suas ressalvas em `notes`.

Rodando nos dados reais, ela já produziu dois resultados que uma ferramenta
otimista teria escondido: um projeto **nasceu junto com o uso de IA** (zero
commits na janela anterior, logo não há linha de base), e outro **caiu** de 9,67
para 4,00 commits por dia ativo depois da adoção.

## Taxas, não totais

Totais não se comparam entre projetos de tamanhos diferentes. A comparação
normaliza por esforço: prompts/hora, US$/hora, taxa de erro em ferramentas,
atribuição e cobertura.

`US$/h` merece cuidado na leitura: **98,6% dos tokens são leitura de cache** e a
saída gerada é 0,2%. O custo acompanha o tamanho do contexto, não o volume
produzido — usar tokens como medida de produção mediria o quanto o contexto
cresceu.

## O fan-out que quase virou um dado

Ao levantar esses números à mão, uma única query juntando sessões e commits
reportou **1.169 horas** para um projeto que tem 28,5. O `JOIN` com commits
multiplica as linhas de sessão, e a soma do esforço conta cada sessão uma vez
por commit ligado.

Por isso `project_comparison` calcula cada projeto com queries escopadas
separadas em vez de um join grande, e há um teste
(`test_project_comparison_does_not_let_commits_inflate_effort`) cuja única
função é impedir que essa regressão volte. Quem escrever consultas próprias para
a monografia deve separar em CTEs.

## O que a ferramenta continua não respondendo

- **Causalidade.** Não há grupo de controle, e a linha de base é descritiva.
- **Qualidade objetiva do código.** Nem cobertura de testes, nem defeitos em
  produção, nem revisão por pares. O proxy disponível é a proporção de commits
  `fix:` e o retrabalho declarado/inferido.
- **Generalização.** Um desenvolvedor, um conjunto de projetos.
