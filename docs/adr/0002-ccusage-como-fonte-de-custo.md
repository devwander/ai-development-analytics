# ADR 0002 - ccusage como fonte de custo, e seus limites

**Status:** aceito
**Data:** 2026-08-28
**Versão verificada:** ccusage 20.0.20

## Comandos e formatos reais

`ccusage session --json` devolve:

```json
{ "session": [ { "agent": "claude", "period": "<session-uuid>", "inputTokens": …,
                 "outputTokens": …, "cacheCreationTokens": …, "cacheReadTokens": …,
                 "totalTokens": …, "totalCost": …, "modelsUsed": [...],
                 "modelBreakdowns": [...], "metadata": {"lastActivity": "..."} } ],
  "totals": { ... } }
```

**O ID da sessão está no campo `period`, não em `sessionId`.** A lista não traz
`firstActivity` nem o projeto.

`ccusage session --json --id <uuid>` devolve uma estrutura **diferente**:

```json
{ "sessionId": "...", "totalCost": ..., "totalTokens": ...,
  "entries": [ { "timestamp": "...", "model": "...", "inputTokens": ..., "costUSD": 0.0 } ] }
```

Daí vem o `first_activity` (menor `timestamp`) e a contagem de requisições de
API. O `costUSD` por entrada vem zerado; só `totalCost` é confiável.

Um ID desconhecido devolve `null` com **exit code 0** — não é erro.

## Descoberta crítica: o ccusage deduplica entre sessões

Casamento neste ambiente: 38 sessões no ccusage, todas presentes nos transcripts
(38/38). Das 53 sessões em disco, 15 não aparecem no ccusage:

- 14 são transcripts sem nenhuma mensagem do assistente (sem consumo real);
- **1 não é**: `ce6352a5-…` tem 518 mensagens do assistente e 157M tokens, mas
  compartilha **279 de 279** pares `(message.id, requestId)` com `96f5be24-…`.

O ccusage deduplica por ID de mensagem em todo o conjunto de dados: uma sessão
retomada/bifurcada tem seu consumo atribuído à sessão de origem. No total,
**717 de 6.711 pares (10,7%) aparecem em mais de uma sessão**.

## Decisão

1. O custo vem do ccusage **verbatim**. O sistema nunca recalcula preços.
2. Quando o ccusage não tem linha para a sessão, `collect()` devolve `None` —
   nunca zeros. O agregador cai para o consumo derivado do transcript e registra
   o motivo em `warnings`.
3. Toda `Usage` carrega `source` (`ccusage` ou `claude_transcript`), então a
   proveniência de cada número fica explícita no relatório.
4. O evento `cost-state` do próprio transcript é uma terceira fonte de custo,
   usada como fallback e como verificação cruzada.

## Validação cruzada

Depois de corrigir a deduplicação intra-sessão (ADR 0001), o consumo derivado do
transcript reproduz o ccusage **exatamente** em todas as sessões que não
compartilham mensagens com outro transcript. As que compartilham não podem
bater por construção, e o teste de integração as exclui explicitamente.

Isso dá duas fontes independentes que se confirmam — e um sinal computável
(`ParsedTranscript.usage_message_keys`) para identificar sessões retomadas.

## Consequência para a pesquisa

Somar custo por sessão a partir do ccusage **subestima** sessões retomadas e
superestima a sessão-mãe. Análises agregadas por projeto ou por dia não sofrem;
análises por sessão precisam declarar a fonte.
