import { notFound } from "next/navigation";

import { Card, Notice } from "@/components/ui";
import { ApiError, api, type Message } from "@/lib/api";
import { formatDateTime, formatNumber, shortSessionId } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * The full conversation of a session, in transcript order.
 *
 * Nothing is truncated. Prompts here reach 27,822 characters and replies
 * 19,275, and when the text itself is the object of study a cut-off changes
 * what a reader believes was said.
 *
 * It gets its own page because the volume demands it: one session holds 573
 * conversational messages and 221 KB of text, which would swamp the session
 * detail it used to sit inside.
 */

const PAGE_SIZE = 50;

type View = "conversa" | "prompts" | "tudo";

const VIEWS: Record<View, { label: string; types: string[]; note: string }> = {
  conversa: {
    label: "conversa",
    types: ["human_prompt", "assistant_text"],
    note: "O que foi digitado e o que o modelo respondeu em texto.",
  },
  prompts: {
    label: "só prompts",
    types: ["human_prompt"],
    note: "Apenas o que o desenvolvedor digitou.",
  },
  tudo: {
    label: "com ferramentas",
    types: ["human_prompt", "assistant_text", "assistant_tool_use"],
    note: "A conversa com as chamadas de ferramenta intercaladas, na ordem real.",
  },
};

function buildHref(id: string, view: View, offset: number): string {
  const query = new URLSearchParams();
  if (view !== "conversa") query.set("view", view);
  if (offset > 0) query.set("offset", String(offset));
  const suffix = query.toString();
  return `/sessions/${id}/conversation${suffix ? `?${suffix}` : ""}`;
}

/**
 * A run of consecutive tool calls, collapsed into one line.
 *
 * A session makes hundreds of them back to back; listing each on its own row
 * buries the conversation in scroll for almost no information. The run keeps
 * its sequence span, so a reader can still locate any single call.
 */
interface ToolRun {
  kind: "tools";
  from: number;
  to: number;
  counts: Map<string, number>;
}

type Entry = ToolRun | { kind: "message"; message: Message };

function groupTurns(messages: Message[]): Entry[] {
  const entries: Entry[] = [];
  for (const message of messages) {
    if (message.message_type !== "assistant_tool_use") {
      entries.push({ kind: "message", message });
      continue;
    }
    const previous = entries[entries.length - 1];
    const name = message.content ?? "ferramenta";
    if (previous && previous.kind === "tools") {
      previous.to = message.sequence;
      previous.counts.set(name, (previous.counts.get(name) ?? 0) + 1);
    } else {
      entries.push({
        kind: "tools",
        from: message.sequence,
        to: message.sequence,
        counts: new Map([[name, 1]]),
      });
    }
  }
  return entries;
}

function ToolRunRow({ run }: { run: ToolRun }) {
  const total = [...run.counts.values()].reduce((sum, n) => sum + n, 0);
  const summary = [...run.counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => (count > 1 ? `${name} ×${count}` : name))
    .join(", ");
  return (
    <li className="turn turn-tool">
      <span className="turn-meta mono">
        #{run.from}
        {run.to !== run.from ? `–${run.to}` : ""}
      </span>
      <span className="mono">
        {total === 1 ? "1 chamada" : `${total} chamadas`}: {summary}
      </span>
    </li>
  );
}

/** Role styling: the developer's turn should be findable at a glance. */
function Turn({ message }: { message: Message }) {
  const isPrompt = message.message_type === "human_prompt";
  return (
    <li className={isPrompt ? "turn turn-prompt" : "turn turn-assistant"}>
      <div className="turn-meta">
        <strong>{isPrompt ? "desenvolvedor" : "assistente"}</strong>
        <span className="mono"> #{message.sequence}</span>
        {message.timestamp ? ` · ${formatDateTime(message.timestamp)}` : ""}
        {isPrompt && message.origin_kind !== "human" && " · origem não confirmada"}
        {!isPrompt && message.model ? ` · ${message.model}` : ""}
      </div>
      {/* Whole content, never clipped. */}
      <div className="prose">{message.content}</div>
    </li>
  );
}

export default async function ConversationPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const requested = typeof query.view === "string" ? query.view : "conversa";
  const view: View = requested in VIEWS ? (requested as View) : "conversa";
  const offset = Math.max(0, Number(query.offset ?? 0) || 0);

  let session, page;
  try {
    [session, page] = await Promise.all([
      api.session(id),
      api.messages(id, {
        message_type: VIEWS[view].types,
        limit: PAGE_SIZE,
        offset,
      }),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <>
        <a className="back-link" href={`/sessions/${id}`}>
          ← Sessão
        </a>
        <h1>Conversa</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong>{" "}
          {error instanceof ApiError ? error.message : "Erro inesperado."}
        </Notice>
      </>
    );
  }

  const shown = page.items.length;
  const hasPrevious = offset > 0;
  const hasNext = offset + shown < page.total;
  const characters = page.items.reduce(
    (sum, message) => sum + (message.content?.length ?? 0),
    0,
  );

  return (
    <>
      <a className="back-link" href={`/sessions/${id}`}>
        ← {session.title ?? shortSessionId(session.claude_session_id)}
      </a>
      <h1>Conversa</h1>
      <p className="page-note">
        Transcrição integral, na ordem do transcript e sem cortes. Resultados de
        ferramenta, resumos de compactação e notas do sistema ficam de fora —
        são registrados com papel de usuário, mas ninguém os digitou.
      </p>

      <div className="filters">
        {(Object.keys(VIEWS) as View[]).map((key) => (
          <a
            key={key}
            href={buildHref(id, key, 0)}
            aria-current={view === key ? "true" : undefined}
          >
            {VIEWS[key].label}
          </a>
        ))}
      </div>

      <Card
        title={`${formatNumber(page.total)} mensagem(ns)`}
        subtitle={`${VIEWS[view].note} Exibindo ${shown} nesta página, ${formatNumber(characters)} caracteres.`}
      >
        {shown === 0 ? (
          <p className="empty">Nenhuma mensagem para este filtro.</p>
        ) : (
          <ol className="turns">
            {groupTurns(page.items).map((entry) =>
              entry.kind === "tools" ? (
                <ToolRunRow key={`tools-${entry.from}`} run={entry} />
              ) : (
                <Turn key={entry.message.sequence} message={entry.message} />
              ),
            )}
          </ol>
        )}

        {(hasPrevious || hasNext) && (
          <div className="pager">
            {hasPrevious && (
              <a href={buildHref(id, view, Math.max(0, offset - PAGE_SIZE))}>
                ← anteriores
              </a>
            )}
            <span className="muted">
              {shown === 0 ? 0 : offset + 1}–{offset + shown} de{" "}
              {formatNumber(page.total)}
            </span>
            {hasNext && (
              <a href={buildHref(id, view, offset + PAGE_SIZE)}>próximas →</a>
            )}
          </div>
        )}
      </Card>
    </>
  );
}
