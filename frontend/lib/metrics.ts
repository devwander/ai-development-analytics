/**
 * Single source of truth for what each field means.
 *
 * The definitions live here rather than beside each usage so the same metric
 * is never explained two different ways on two screens - a dashboard meant to
 * back a study cannot afford a term that drifts between pages.
 *
 * Each entry is one sentence, written to answer "what exactly is this number?"
 * and, where the answer is easy to misread, "what is it *not*?".
 */

export const HINTS = {
  // -- effort -------------------------------------------------------------
  activeTurn:
    "Soma da duração dos turnos em que o Claude Code esteve efetivamente processando. É o proxy de tempo de trabalho — não inclui pausas nem o tempo em que a sessão ficou aberta sem uso.",
  wallClock:
    "Intervalo entre o primeiro e o último evento da sessão. Sessões atravessam dias, então isto não é tempo de trabalho e não serve como denominador de produtividade.",
  sessions:
    "Cada arquivo de transcript do Claude Code é uma sessão. Uma sessão retomada gera um arquivo novo.",
  humanPrompts:
    "Mensagens que o desenvolvedor realmente digitou. Exclui resultados de ferramenta, resumos de compactação e notas injetadas pelo sistema — todos gravados com papel de usuário no transcript.",
  confirmedPrompts:
    "Subconjunto dos prompts em que o Claude Code marcou explicitamente a origem como humana. Versões antigas não gravavam esse campo, então pode ser menor que o total.",
  injectedMessages:
    "Mensagens com papel de usuário que o próprio Claude Code inseriu: resumo gerado ao compactar a conversa e notas de sistema.",
  assistantMessages:
    "Mensagens de API do modelo. O Claude Code grava um registro por bloco de conteúdo, então o número de registros no transcript é sempre maior.",
  toolCalls:
    "Total de invocações de ferramenta (Bash, Edit, Read, MCP etc.) feitas pelo modelo.",
  toolErrors:
    "Chamadas de ferramenta que retornaram erro. Não implica falha da tarefa — um grep sem resultado também conta.",
  thinkingBlocks:
    "Blocos de raciocínio interno emitidos pelo modelo. Indica quanto a tarefa exigiu deliberação.",
  promptsPerHour:
    "Prompts humanos por hora de trabalho. Densidade de interação: um valor alto pode ser diálogo produtivo ou dificuldade de alinhamento — distinguir os dois exige a percepção declarada.",
  medianSession:
    "Mediana da duração de trabalho das sessões. Resistente a uma sessão longa isolada, ao contrário da média.",
  concentration:
    "Parcela de todo o tempo de trabalho que está no quinto de sessões mais longas. Alto significa que poucas sessões dominam o conjunto.",

  // -- consumption --------------------------------------------------------
  tokens:
    "Soma de tokens de entrada, saída, escrita e leitura de cache. Quase tudo é leitura de cache, então isto mede o tamanho do contexto, não o volume produzido.",
  cost: "Custo equivalente em dólares, reportado pelo ccusage. Nunca recalculado aqui. 'n/d' significa que nenhuma fonte pôde determiná-lo — não que foi zero.",
  costPerHour:
    "Custo por hora de trabalho. Acompanha o tamanho do contexto mantido, não a quantidade de código produzido.",
  usageSource:
    "Qual fonte forneceu estes números. ccusage e transcript descrevem os mesmos tokens e discordam por construção — por isso nunca são somados.",
  cacheRead:
    "Tokens lidos do cache de contexto, cobrados a uma fração do preço. É a maior parte do consumo em sessões longas.",
  cacheWrite:
    "Tokens gravados no cache de contexto na primeira vez que aquele trecho é enviado.",

  // -- delivery -----------------------------------------------------------
  attribution:
    "Teto da parcela de linhas commitadas que passou por uma edição rastreada da IA. O numerador ignora arquivos escritos via shell e o denominador conta o commit inteiro, incluindo o digitado à mão — logo é um limite superior, nunca uma medida de autoria.",
  coverage:
    "Fração das sessões que têm ao menos um commit associado. É a amostra real disponível para qualquer afirmação sobre entrega.",
  linkedCommits:
    "Commits com evidência de ligação à sessão: sobreposição de arquivos com o que a IA editou, ou o ID da sessão citado na mensagem. Associação, não prova de autoria.",
  filesTouched:
    "Arquivos que a sessão leu ou alterou, conforme registrado no transcript. Arquivos editados por comandos de shell não aparecem aqui.",
  filesOutsideProject:
    "Arquivos de trabalho fora da árvore do projeto (capturas de tela, HTML temporário). Não contam como alteração de código.",
  churn:
    "Linhas adicionadas e removidas, extraídas do diff que o Claude Code grava em cada edição. Disponível apenas para Edit e Write.",

  // -- validity -----------------------------------------------------------
  claudeVersions:
    "Quantas versões diferentes do Claude Code aparecem no período. Se for mais de uma, a ferramenta mudou durante a coleta — é variável de confusão.",
  shellEditing:
    "Sessões com muitas chamadas de ferramenta e nenhum arquivo rastreado: provavelmente editaram via shell, o que o transcript não registra.",
  resumedSessions:
    "Sessões cujo consumo o ccusage atribuiu à sessão de origem, por compartilharem mensagens. O custo delas aparece somado na sessão-mãe.",
  corruptedLines:
    "Linhas do transcript que não puderam ser interpretadas. O arquivo original permanece intacto; a coleta apenas as ignora e as contabiliza.",
  sessionStatus:
    "Heurístico: o Claude Code não grava um marcador de fim. 'ativa' é um transcript alterado recentemente; 'interrompida' indica um pedido cancelado.",
  interrupted:
    "Sessões em que um pedido foi cancelado pelo desenvolvedor antes de concluir.",

  // -- declared and inferred ---------------------------------------------
  perceivedQuality:
    "Declarado pelo desenvolvedor, escala 1 a 5. Percepção de qualidade não está em nenhum transcript e não pode ser inferida — só respondida.",
  perceivedProductivity:
    "Declarado pelo desenvolvedor, escala 1 a 5. Sensação de produtividade, independente do que as métricas objetivas mostram.",
  perceivedEffort:
    "Declarado pelo desenvolvedor, escala 1 a 5, onde 5 é o mais pesado. Esforço mental, não tempo decorrido.",
  trust:
    "Declarado pelo desenvolvedor, escala 1 a 5. Quanta confiança a saída da IA inspirou naquela sessão.",
  reworkReported:
    "O desenvolvedor declarou ter precisado refazer trabalho. Comparável com o retrabalho que a análise por IA infere — a divergência entre os dois é informativa.",
  taskType:
    "Categoria inferida por um modelo de linguagem a partir de um resumo da sessão. Dado inferido, não observado.",
  aiAnalysis:
    "Produzido por um modelo de linguagem sobre um resumo limitado da sessão, nunca sobre o transcript completo. Acompanha um nível de confiança.",
  confidence:
    "Quanto o modelo julga que a evidência fornecida sustenta a análise. Cai quando o resumo enviado foi muito truncado.",

  // -- baseline -----------------------------------------------------------
  baseline:
    "Atividade no Git em duas janelas do mesmo repositório, separadas pela adoção da IA. Comparação descritiva: equipe, fase e escopo também mudam entre as janelas.",
  adoptionDate:
    "Marco entre 'antes' e 'depois'. Por padrão é a primeira sessão registrada do projeto, que é quando o Claude Code chegou ali.",
  activeDays:
    "Dias distintos com ao menos um commit. Serve de denominador porque janelas de tamanhos diferentes tornam totais incomparáveis.",
  commitsPerActiveDay:
    "Commits divididos por dias ativos. Sobrevive a janelas de duração desigual, ao contrário do total.",

  // -- provenance ---------------------------------------------------------
  noModelResponse:
    "Sessões em que o transcript existe mas o modelo nunca respondeu — o Claude Code foi aberto, houve digitação, e a sessão acabou antes de qualquer chamada à API. Não têm custo porque não houve requisição, e é por isso que alguns dias mostram sessões sem gasto.",
  auditability:
    "Uma sessão só é auditável enquanto seus bytes originais existem em algum lugar. O Claude Code remove os próprios transcripts com o tempo, então a coleta guarda uma cópia verificada por SHA-256 — sessões coletadas antes disso podem já ter perdido a origem.",
  withoutProject:
    "Sessões que não puderam ser associadas a nenhum projeto, por não terem diretório de trabalho registrado. Ficam invisíveis nas telas de projeto, e é por isso que o total geral pode ser maior que a soma dos projetos.",
  provenance:
    "Caminho e SHA-256 do transcript que gerou estes dados, permitindo refazer o percurso métrica → sessão → evento → arquivo original.",
  projectPath:
    "Raiz do projeto: o repositório Git quando existe, senão o diretório comum a todos os cwd da sessão. Nunca o nome do diretório do Claude Code, que é uma codificação com perda.",
} as const;

export type HintKey = keyof typeof HINTS;
