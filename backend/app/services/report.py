"""Render a SessionReport as the human-readable text report from the brief."""

from __future__ import annotations

from datetime import datetime

from app.schemas.observed import SessionReport, Usage


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _fmt_tokens(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "unknown"


def _preferred_usage(usages: list[Usage]) -> Usage | None:
    """ccusage is the reference for cost; the transcript is the fallback."""
    for usage in usages:
        if usage.source == "ccusage":
            return usage
    return usages[0] if usages else None


def render_text_report(report: SessionReport) -> str:
    session = report.session
    usage = _preferred_usage(report.usage)
    lines: list[str] = []

    def section(title: str) -> None:
        lines.extend(["", "-" * 60, title, "-" * 60])

    lines.append(f"SESSION {session.claude_session_id}")
    if session.title:
        lines.append(f'"{session.title}"')
    lines.append("")
    lines.append(f"Projeto:    {session.project_name or 'unknown'}")
    lines.append(
        f"Caminho:    {session.project_path or 'unknown'}"
        f"  (via {session.project_path_source or 'indeterminado'})"
    )
    lines.append(
        f"Periodo:    {_fmt_dt(session.started_at)} -> {_fmt_dt(session.ended_at)}"
    )
    lines.append(f"Duracao:    {_fmt_duration(session.duration_seconds)}")
    if report.metrics.active_turn_seconds:
        lines.append(
            f"Turnos:     {_fmt_duration(report.metrics.active_turn_seconds)} de turnos ativos"
        )
    lines.append(f"Status:     {session.status.value}")
    lines.append(f"Modelos:    {', '.join(session.models_used) or 'unknown'}")
    if usage:
        lines.append(f"Tokens:     {_fmt_tokens(usage.total_tokens)} (fonte: {usage.source})")
        cost = f"US$ {usage.cost_usd:.2f}" if usage.cost_usd is not None else "unknown"
        lines.append(f"Custo eq.:  {cost}")

    section("OBSERVADO - INTERACAO")
    metrics = report.metrics
    lines.append(f"Prompts do usuario:   {metrics.human_prompt_count}")
    lines.append(f"Mensagens do modelo:  {metrics.assistant_message_count}")
    lines.append(f"Chamadas de tool:     {metrics.tool_call_count}")
    lines.append(f"Tools com erro:       {metrics.tool_error_count}")
    lines.append(f"Blocos de raciocinio: {metrics.thinking_block_count}")
    lines.append(
        f"Arquivos tocados:     {metrics.files_touched} "
        f"({metrics.files_outside_project} fora do projeto)"
    )

    if session.first_prompt:
        section("PRIMEIRO PROMPT")
        lines.append(_truncate(session.first_prompt, 600))

    section("FERRAMENTAS")
    if report.tool_stats:
        width = max(len(s.tool_name) for s in report.tool_stats)
        for stat in report.tool_stats:
            suffix = f"  ({stat.errors} erro(s))" if stat.errors else ""
            lines.append(f"{stat.tool_name.ljust(width)}  {stat.calls}{suffix}")
    else:
        lines.append("nenhuma")

    section("ARQUIVOS ENVOLVIDOS")
    inside = [f for f in report.files if f.is_within_project is not False]
    outside = [f for f in report.files if f.is_within_project is False]
    if inside:
        root = session.project_path
        for touch in inside:
            churn = ""
            if touch.insertions or touch.deletions:
                churn = f"  (+{touch.insertions or 0}/-{touch.deletions or 0})"
            shown = touch.file_path
            if root and shown.startswith(root + "/"):
                shown = shown[len(root) + 1:]
            lines.append(f"[{touch.change_type.value:>8}] {shown}{churn}")
    else:
        lines.append("nenhum")
    if outside:
        lines.append("")
        lines.append(
            f"+ {len(outside)} arquivo(s) fora do projeto (scratch/temporarios), "
            "nao contabilizados como alteracao de codigo"
        )

    section("TOKENS POR FONTE")
    for item in report.usage:
        cost = f"US$ {item.cost_usd:.4f}" if item.cost_usd is not None else "unknown"
        lines.append(
            f"{item.source:>18}: total={_fmt_tokens(item.total_tokens)} "
            f"in={item.input_tokens} out={item.output_tokens} "
            f"cache_w={item.cache_creation_tokens} cache_r={item.cache_read_tokens} "
            f"custo={cost}"
        )
        for model in item.models:
            lines.append(
                f"{'':>20}- {model.model}: {_fmt_tokens(model.total_tokens)}"
            )
        for note in item.notes:
            lines.append(f"{'':>20}# {note}")

    section("GIT")
    git = report.git
    if git is None:
        lines.append("coleta desativada")
    elif not git.is_repository:
        lines.append(f"projeto sem repositorio git ({git.error or 'nao encontrado'})")
    else:
        lines.append(f"Repositorio: {git.repository_root}")
        lines.append(f"Remote:      {git.repository_url or 'nenhum'}")
        lines.append(f"Branch HEAD: {git.current_branch or 'unknown'}")
        if git.session_branches:
            lines.append(f"Branch(es) durante a sessao: {', '.join(git.session_branches)}")
        lines.append(
            f"Working tree: {'com alteracoes' if git.working_tree_dirty else 'limpo'}"
            f" ({git.working_tree_changes} arquivo(s))"
        )
        lines.append(f"Commits na janela: {len(git.commits_in_window)}")
        by_hash = {c.commit_hash: c for c in git.commits_in_window}
        related = [c for c in git.correlations if c.likely_related]
        lines.append(f"Provavelmente relacionados: {len(related)}")
        for corr in git.correlations:
            commit = by_hash.get(corr.commit_hash)
            if commit is None:
                continue
            flag = "LIKELY RELATED" if corr.likely_related else "sem evidencia de ligacao"
            lines.append("")
            lines.append(f"  {commit.commit_hash[:10]}  {commit.message}")
            lines.append(
                f"    autor: {commit.author_name}  |  {_fmt_dt(commit.committed_at)}"
            )
            lines.append(
                f"    arquivos: {commit.files_changed}  +{commit.insertions}/-{commit.deletions}"
            )
            lines.append(f"    associacao: {flag}")
            for reason in corr.reasons:
                lines.append(f"      - {reason}")

    section("PROVENIENCIA")
    source = report.source
    if source:
        lines.append(f"Transcript: {source.transcript_path}")
        lines.append(f"SHA-256:    {source.transcript_sha256}")
        lines.append(
            f"Linhas:     {source.parsed_lines} lidas / "
            f"{source.unparseable_lines} corrompidas / {source.total_lines} total"
        )
        lines.append(f"Collector:  {source.collector_version}")
        if source.unknown_event_types:
            lines.append(f"Eventos desconhecidos: {source.unknown_event_types}")

    section("ANALISE (INFERIDA POR IA)")
    lines.append("nao executada - AI_ANALYSIS_ENABLED=false")

    if report.warnings:
        section("AVISOS")
        for warning in report.warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " [...]"
