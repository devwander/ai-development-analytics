# ADR 0006 - Análise por LLM

**Status:** aceito
**Data:** 2026-08-28
**Provider implementado:** Anthropic (SDK oficial `anthropic` 1.2.0)

## O que o LLM decide, e o que ele não decide

A regra do §31 — *não usar o LLM para determinar métricas que podem ser obtidas
diretamente dos dados* — define a fronteira. Tokens, durações, contagens de
ferramenta e listas de arquivos já são medidos; pedir ao modelo que os repita só
criaria oportunidade de contradizer o registro.

O modelo recebe esses fatos prontos, com a instrução explícita de não os
reformular, e responde apenas o que é interpretação: objetivo, resumo,
categoria, complexidade, resultado, retrabalho, tecnologias e problemas.

Nenhum campo inferido mora fora de `ai_analyses`.

## Saída estruturada é imposta pela API, não parseada de texto

`client.messages.parse(..., output_format=AnalysisResult)` valida a resposta
contra o schema Pydantic. É isso que torna real o requisito "a resposta do LLM
deve ser obrigatoriamente estruturada" (§13), em vez de aspiracional.

Há ainda uma segunda barreira que schema nenhum expressa: `validate_result`
rejeita `requires_rework: true` sem `rework_evidence`, e o contrário. Uma
resposta que viola isso **não é gravada** — é exatamente a conclusão infundada
que o §12 proíbe.

## Retrabalho: sinais calculados, julgamento delegado

`app/analyzers/signals.py` computa candidatos **a partir das linhas do banco**,
de forma determinística:

| Sinal | O que observa |
|---|---|
| `correction_request` | prompt humano com linguagem de correção (PT e EN) |
| `repeated_edit_bursts` | mesmo arquivo editado em ≥3 rajadas separadas por 20 min |
| `failing_test_runs` | comando de teste reconhecido que saiu com erro |
| `repeated_tool_errors` | mesma ferramenta falhando ≥3 vezes |
| `reverted_commit` | commit associado cuja mensagem começa com `revert` |

Duas escolhas importam:

- **Rajadas, não contagem.** Contar edições sozinho marcaria qualquer refatoração
  grande. Separar por um intervalo de 20 minutos é o que distingue "escreveu de
  uma vez" de "voltou nele depois".
- **Só prompts humanos reais.** Um `tool_result` contendo "Error: that's wrong"
  não é o desenvolvedor pedindo correção. A distinção do ADR 0001 é o que torna
  isso possível.

E os sinais são **candidatos, não conclusões**. Rodando contra as sessões reais
desta máquina, o primeiro acerto foi um falso positivo instrutivo: *"me corrija
se estiver errado"* casa com o padrão de correção sem ser um pedido de correção.
O digest diz isso ao modelo explicitamente e manda ignorar sinais que o contexto
não sustenta.

## Digest limitado: 415M tokens não cabem em um prompt

A sessão `f50b5adf` tem 2.396 registros e 415M tokens de contexto acumulado.
O digest dela tem **12,7 KB**. Medido nas seis maiores sessões: 6 KB a 16 KB.

A compressão é `_sample_middle_out`: preserva o início e o fim, descarta o meio.
A intenção de uma sessão está no começo e o desfecho no fim; o meio é onde está
a moagem, e é o mais barato de largar.

Nada é omitido em silêncio. Toda truncagem entra em `## WHAT WAS OMITTED FROM
THIS DIGEST`, com a instrução de baixar a confiança — o modelo precisa saber que
não viu tudo.

O digest também cita a **mesma fonte de usage que a API** (ADR 0005), para o
número no prompt nunca divergir do número no dashboard.

## Privacidade: redação ligada por padrão

O §19 exige que transcripts não sejam enviados automaticamente. A análise é
opt-in (`AI_ANALYSIS_ENABLED=false`), mas uma vez ligada o digest viaja a
terceiros — então `LLM_REDACT_ENABLED=true` é o padrão, e desligar é decisão
deliberada.

O que sai: prefixo do diretório home (carrega o nome de usuário), e-mails,
credenciais embutidas em URL, e strings com cara de chave de API (`sk-…`,
`ghp_…`, `glpat-…`, `AKIA…`, JWT).

O que fica: o caminho relativo ao projeto. `frontend/src/features/patients/
PatientListView.tsx` é justamente o sinal que o modelo precisa; `/home/<alguém>/`
não é.

`analyze --dry-run --show-digest` imprime exatamente o que sairia da máquina,
antes de qualquer envio. Medido nas cinco maiores sessões: 6 a 57 valores
redigidos por digest.

## Abstração de provider

`LLMProvider` tem **um** método: dado system, prompt e um schema Pydantic,
devolver uma instância validada. Nada de tool use, streaming ou formato de
mensagem de fornecedor atravessa essa fronteira — acrescentar um provider é
implementar um método.

Apenas **Anthropic** está implementado. `openai` e `local` estão registrados
como falha explícita, não como código não testado. Sem credencial para verificar
contra o endpoint real, um cliente que *parece* funcionar é pior que um "não
implementado": o primeiro sinal de problema seria uma análise errada gravada
como dado.

## Semântica de erro

| Situação | Resultado |
|---|---|
| `AI_ANALYSIS_ENABLED=false` | `AnalysisDisabled` → HTTP 503, nada enviado |
| Sem credencial / provider não implementado | `LLMNotConfigured` → HTTP **503** |
| Provider fora do ar, recusa, resposta inválida | `LLMUnavailable` → HTTP **502** |
| Resposta viola o contrato de retrabalho | `AnalysisRejected` → HTTP **502**, nada gravado |

A distinção 503/502 é deliberada: falta de configuração é problema de quem
chama, não uma análise que falhou. Em nenhum desses caminhos uma análise
fabricada chega ao banco.

## Custo

O system prompt é idêntico para toda sessão e vai com `cache_control:
ephemeral` — numa execução em lote isso é a diferença entre pagar o prompt uma
vez e pagá-lo N vezes. `LLM_EFFORT=medium` é o padrão; a tarefa é
classificação e sumarização sobre evidência já reunida, não raciocínio
aberto.

## Estado de verificação

O pipeline foi testado ponta a ponta com um provider stub determinístico e
contra os dados reais desta máquina para digest, sinais e redação. **O
`AnthropicProvider` não foi exercitado contra a API real** — não há credencial
neste ambiente. O caminho de rede permanece não verificado até alguém definir
`LLM_API_KEY` e rodar `analyze`.
