import { NextRequest, NextResponse } from "next/server";

import { ApiError, api } from "@/lib/api";

/**
 * Bundles the full export of one or more sessions into a single downloadable
 * JSON file. Runs server-side so it can reach the analytics API directly -
 * the browser only ever talks to this route, never to the API origin.
 *
 * Fed by a plain GET form (?id=1&id=2&...): no client JavaScript is required
 * to trigger a download, matching the rest of the dashboard.
 */

export const dynamic = "force-dynamic";

const MAX_SESSIONS = 100;

export async function GET(request: NextRequest) {
  const ids = Array.from(new Set(request.nextUrl.searchParams.getAll("id").filter(Boolean)));

  if (ids.length === 0) {
    return NextResponse.json(
      { error: "Nenhuma sessão selecionada." },
      { status: 400 },
    );
  }
  if (ids.length > MAX_SESSIONS) {
    return NextResponse.json(
      { error: `Selecione no máximo ${MAX_SESSIONS} sessões por download.` },
      { status: 400 },
    );
  }

  const results = await Promise.allSettled(ids.map((id) => api.sessionExport(id)));

  const sessions: Awaited<ReturnType<typeof api.sessionExport>>[] = [];
  const errors: string[] = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      sessions.push(result.value);
    } else {
      const reason = result.reason;
      const message =
        reason instanceof ApiError
          ? reason.message
          : reason instanceof Error
            ? reason.message
            : "erro desconhecido";
      errors.push(`sessão ${ids[index]}: ${message}`);
    }
  });

  if (sessions.length === 0) {
    return NextResponse.json({ error: "Nenhuma sessão pôde ser exportada.", errors }, { status: 502 });
  }

  const payload = {
    generated_at: new Date().toISOString(),
    session_count: sessions.length,
    sessions,
    ...(errors.length > 0 ? { errors } : {}),
  };

  const filename =
    sessions.length === 1
      ? `sessao-${sessions[0].session.claude_session_id}.json`
      : `sessoes-${sessions.length}-${new Date().toISOString().slice(0, 10)}.json`;

  return new NextResponse(JSON.stringify(payload, null, 2), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": `attachment; filename="${filename}"`,
    },
  });
}
