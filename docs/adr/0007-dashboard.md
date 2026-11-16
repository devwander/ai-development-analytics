# ADR 0007 - Dashboard

**Status:** aceito
**Data:** 2026-08-31
**Stack:** Next.js 15 (App Router), React 19, TypeScript. Sem biblioteca de gráficos.

## Observado e inferido são visualmente separados

A separação que atravessa todo o sistema precisa sobreviver à tela, senão o
dashboard desfaz o que a coleta construiu.

- A visão geral abre dizendo que tudo ali é dado observado.
- "Tarefas por categoria" — o único agregado inferido — leva o subtítulo *"Dado
  inferido por IA — não é observação"*, e fica vazio até a análise rodar.
- No detalhe da sessão, a análise é um cartão isolado, rotulado como inferido, e
  exibe o provedor, o modelo e a data que a produziram.

## Nenhum número aparece sem a fonte

`usage_source` viaja junto de todo token e custo: na listagem de sessões é uma
coluna, no detalhe é a primeira coluna da tabela por fonte, marcada com *"usada
nos totais"*. A tabela por fonte traz o subtítulo *"nunca some as linhas"*,
porque somá-las dobra os números (ADR 0002).

Custo `null` é renderizado como **n/d**, nunca como `US$ 0,00`. Transformar
"desconhecido" em "zero" seria inventar dado na última camada.

## Trabalho e duração aparecem lado a lado

O tile grande do detalhe é **trabalho** (turnos ativos); duração (tempo de
parede) fica ao lado, em cinza, rotulada como tal. Numa sessão real isso é
2h57m contra 118h16m — 2,5% de densidade. Exibir só a duração convidaria a usá-la
como denominador de produtividade, o que estaria errado por duas ordens de
grandeza.

## Gráficos

Dois gráficos, nunca dois eixos y. Sessões/dia e custo/dia medem coisas de escala
diferente e por isso são cartões separados — eixo duplo é o erro mais comum em
dashboards e não aparece aqui.

Especificações aplicadas em cada coluna: máximo de 24px de espessura, 2px de
folga entre vizinhas, cantos arredondados só na ponta e retos na linha de base,
grade em hairline recessiva, **um único rótulo direto** (o máximo — número em
toda coluna é ruído) e tooltip por coluna cuja área de acerto cobre a faixa
inteira, para uma coluna de 1px continuar hoverável.

Ticks do eixo arredondam para números limpos: `US$ 250`, não `US$ 250.00`.

Série única não leva legenda — o título do cartão já diz o que está plotado.

## Cores

A paleta foi **validada por script**, não escolhida a olho:

- categórica clara: todos os testes passam, com WARN de contraste em três slots;
- categórica escura: todos os testes passam, inclusive contraste.

O WARN obriga alívio — e o gráfico de categorias entrega os dois: rótulo direto
em cada barra **e** uma tabela expansível. Identidade nunca depende só da cor.

Dark mode é **escolhido**, não invertido: são os mesmos oito matizes reescalonados
para a superfície escura, declarados tanto sob `prefers-color-scheme` quanto sob
`[data-theme]`, para o ajuste do usuário vencer nos dois sentidos.

Cores de status (ativa/concluída/interrompida) são reservadas, distintas das
séries, e sempre acompanham um rótulo textual.

Além do oitavo slot a cauda vira "outros". Um nono matiz gerado é
indistinguível de um existente sob deficiência de visão de cores.

## Os mesmos painéis, em dois recortes

A visão geral e a página de um projeto mostram os mesmos indicadores — a única
diferença é o escopo. Por isso os painéis (KPIs, gráficos diários, status e
categorias) vivem em `components/OverviewPanels.tsx` e são consumidos pelas duas.

Isso não é economia de linhas: é a garantia de que uma métrica que ganha uma
ressalva ganha nas duas telas, e de que os dois recortes nunca contam histórias
diferentes sobre o mesmo número.

O tile "Projetos" some no recorte de um projeto — "Projetos: 1" é ruído. Em
troca, a página do projeto acrescenta o que só faz sentido ali: a distribuição
de ferramentas com participação percentual, e as sessões recentes.

## Cada campo carrega sua definição

Todo indicador tem um `ⓘ` que revela uma frase explicando o que aquele número é
— e, onde a leitura é fácil de errar, o que ele **não** é. "Tempo de parede" diz
que não serve como denominador de produtividade; "Atribuição" diz que é um teto,
não autoria; "n/d" no custo diz que nenhuma fonte pôde determiná-lo, não que foi
zero.

As definições ficam em `lib/metrics.ts`, não ao lado de cada uso. Um dashboard
que sustenta uma pesquisa não pode ter um termo explicado de duas maneiras em
duas telas, e a fonte única também dá o texto pronto para citar na monografia.
Um verificador confirma que não há definição órfã nem termo usado sem definição.

A dica usa o `title` nativo de propósito: dispensa JavaScript (funciona em
Server Components), sobrevive a impressão e a modos de leitura, e o `span` é
focalizável para a definição ser alcançável por teclado, não só por ponteiro.

## Filtro de período

Uma linha só, alinhada à esquerda, acima de tudo que ela escopa — nunca dentro
de um cartão, nunca por gráfico. Presets antes do intervalo customizado, porque
ninguém quer brigar com um calendário para pedir "últimos 30 dias".

Construído com links e um `form method="get"`: funciona em Server Components sem
JavaScript, e o intervalo vive na URL — o que torna qualquer recorte um endereço
compartilhável e citável na monografia.

**O filtro escopa tudo abaixo dele**, inclusive cobertura e atribuição, que
passam a descrever apenas aquela janela. O texto do filtro diz isso, porque um
teto de atribuição de um mês lido como se fosse do projeto inteiro seria uma
conclusão errada.

Três decisões de correção:

- **A linha de base não é filtrada.** Ela é um par fixo de janelas em torno da
  data de adoção; re-fatiá-la por um intervalo arbitrário responderia em silêncio
  a uma pergunta diferente da que ela foi calculada para responder.
- **Na listagem de projetos, a data entra no `JOIN`, não no `WHERE`.** Num outer
  join, um `WHERE` sobre a tabela juntada vira inner join na prática, e projetos
  sem sessão na janela sumiriam do diretório em vez de mostrar zeros.
- **Sessão sem data de início fica fora de qualquer janela.** São 6 aqui —
  exatamente as mesmas que não têm projeto. Mantê-las faria os totais filtrados
  discordarem da lista filtrada.

Um intervalo invertido é descartado na leitura dos parâmetros, não enviado. A
API o rejeita com 422, e uma página que repassasse isso diria ao leitor que a API
está fora quando na verdade as datas estão trocadas.

## Valor ausente não é desenhado como zero

Alguns dias mostram sessões e nenhum custo, e a barra de altura zero era
indistinguível de um dia que custou zero de verdade. A causa é real e vale
registrar: **14 das 55 sessões nunca receberam resposta do modelo** — o Claude
Code foi aberto, houve digitação, e a sessão acabou antes de qualquer chamada à
API. Sem requisição não há custo, então o valor é `null`, não `0`.

Dias assim agora aparecem como **contorno tracejado na linha de base**: presente,
medido, indeterminado. Desenhá-los como zero contradiz a regra que o resto da
aplicação segue desde a coleta, e era o último lugar onde "desconhecido" ainda
virava "zero".

O rótulo direto (o máximo) também ignora valores ausentes — pousar sobre um deles
anunciaria um pico que não foi medido.

O painel de validade passou a contar essas sessões, para a causa ficar visível
sem precisar caçá-la no gráfico.

## A conversa completa tem página própria

Ler o que foi digitado e o que o modelo respondeu é a matéria-prima da análise
qualitativa, então a transcrição é entregue **integral**: um prompt aqui chega a
27.822 caracteres e uma resposta a 19.275, e cortar qualquer um dos dois muda o
que o leitor acredita que foi dito.

Ganhou página própria porque o volume exige: uma sessão tem **573 mensagens de
conversa e 221 KB de texto**, que sufocariam o detalhe da sessão onde a lista
truncada morava. O detalhe agora mostra os cinco primeiros prompts como prévia e
leva para lá.

Três recortes: `conversa` (prompts + respostas), `só prompts`, e `com
ferramentas` — este último intercala as chamadas na ordem real, que é o que
mostra *como* a sessão andou.

**Sequências de ferramentas são agrupadas.** Uma sessão faz centenas de chamadas
seguidas; listar cada uma numa linha soterra a conversa em rolagem por quase
nenhuma informação. Um trecho vira `#3–84 · 41 chamadas: mcp__gitlab__get_issue
×18, Bash ×16, …`, preservando o intervalo de sequência para qualquer chamada
individual continuar localizável.

Resultados de ferramenta, resumos de compactação e notas do sistema ficam de
fora: são gravados com papel de usuário, mas ninguém os digitou (ADR 0001).

## Só leitura

O dashboard não escreve nada. A coleta continua na CLI e a análise é disparada
por lá ou pela API. Isso mantém o processo do dashboard longe dos dados locais
do Claude Code.

## Falhas são explícitas

Se a API estiver fora, cada página mostra um aviso dizendo o que subir, em vez de
uma tela vazia ou de números zerados. Sessão inexistente devolve 404 do próprio
Next.

## Notas do coletor não são traduzidas

O aviso *"411 assistant record(s) repeated the usage…"* aparece em inglês numa
interface em português de propósito: é saída verbatim do coletor, gravada junto
da linha. Reescrevê-la na UI faria o texto exibido divergir do texto armazenado.
A lista recebeu o título "Notas do coletor" para a origem ficar clara.

## Defeitos que só a renderização revelou

O validador checa cor; layout e formatação precisam de olho. Inspecionando as
screenshots apareceram quatro coisas que nenhum teste automático teria pego:

1. Ticks do eixo de custo mostrando centavos (`US$ 250.00`) — ruído numa linha
   de grade. Ticks agora arredondam; o rótulo direto e o tooltip mantêm a
   precisão.
2. `Custo eq.Fonte` colados no cabeçalho: uma célula alinhada à direita zera o
   próprio `padding-right` para encostar os dígitos na borda, então a coluna
   seguinte precisa do seu próprio `padding-left`.
3. `US$ 247,08` quebrando entre o símbolo e os dígitos — resolvido com
   `white-space: nowrap` nas células numéricas.
4. **O filtro dizia "01/08 a 15/08" e o resumo logo abaixo dizia "31/07 a
   14/08".** `new Date("2026-08-01")` é meia-noite UTC, que em UTC−3 renderiza
   como o dia anterior. Datas sem hora agora são formatadas a partir dos próprios
   dígitos, sem passar por `Date` — é a armadilha de fuso que já estava
   documentada em `lib/format.ts`, mordendo num valor date-only.
5. **`US$ 0,00` numa sessão que custou US$ 0,000493.** O valor era real; o
   `toFixed(2)` é que o arredondava para algo que se lê como "grátis". Valores
   abaixo de meio centavo agora aparecem como `< US$ 0,01` — pela mesma razão
   que um custo desconhecido nunca vira zero.

Na mesma passada, os separadores foram unificados em pt-BR: `US$ 1.592` com
ponto de milhar ao lado de `US$ 247.08` com ponto decimal era genuinamente
ambíguo. Agora é `US$ 247,08` e `269,7M`.

## Verificação

Todas as rotas respondem 200 — 404 para sessão inexistente, para projeto
inexistente e para id não-numérico. `tsc --noEmit` limpo, build de produção
passando, e as páginas foram **renderizadas e inspecionadas em screenshot** nos
modos claro e escuro.
