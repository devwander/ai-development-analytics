"""Minimal CLI for exercising the collectors before any database exists.

    python -m app.cli.main doctor
    python -m app.cli.main projects
    python -m app.cli.main sessions [--project KEY] [--limit N]
    python -m app.cli.main report SESSION_ID [--json] [--out DIR]

    python -m app.cli.main sync [--project KEY] [--limit N] [--dry-run]
    python -m app.cli.main db-projects
    python -m app.cli.main db-sessions [--limit N] [--project-id ID]
    python -m app.cli.main show SESSION_ID

    python -m app.cli.main analyze SESSION_ID [--dry-run]
    python -m app.cli.main analyze --all [--limit N] [--dry-run]

    python -m app.cli.main survey SESSION_ID [--quality 1-5] [--productivity 1-5] ...
    python -m app.cli.main baseline [--project-id N] [--adoption YYYY-MM-DD] [--days 90]
    python -m app.cli.main compare
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.collectors.ccusage.collector import (
    CcusageCollector,
    CcusageError,
    CcusageRunner,
)
from app.collectors.claude.discovery import (
    SessionFile,
    discover_projects,
    discover_sessions,
    find_session_file,
    resolve_claude_data_path,
    resolve_project_paths,
)
from app.collectors.git.collector import find_repository_root
from app.analyzers.service import AnalysisDisabled, SessionAnalyzer
from app.config import get_settings
from app.db.base import session_scope
from app.llm.base import LLMNotConfigured
from app.models.entities import AIAnalysis, Project as ProjectRow, SessionSurvey
from app.services import analytics as analytics_service
from app.services.baseline import BaselineError, baseline_rates, compute_baseline
from app.models.entities import GitCommit, Project, Session, SessionCommitLink, Usage
from app.services.aggregator import SessionAggregator
from app.services.report import render_text_report
from app.services.sync import SyncDecision, SyncService

DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[3] / "reports"


def _data_path(args: argparse.Namespace) -> Path:
    return resolve_claude_data_path(
        args.claude_data_path or os.environ.get("CLAUDE_DATA_PATH")
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    print("== Claude Code ==")
    try:
        data_path = _data_path(args)
    except FileNotFoundError as exc:
        print(f"  ERRO: {exc}")
        return 1
    projects = discover_projects(data_path)
    sessions = [s for p in projects for s in p.session_files]
    print(f"  data path : {data_path}")
    print(f"  projetos  : {len(projects)}")
    print(f"  sessoes   : {len(sessions)}")
    print(f"  vazias    : {sum(1 for s in sessions if s.is_empty)}")

    print("== ccusage ==")
    runner = CcusageRunner(binary=os.environ.get("CCUSAGE_BIN", "ccusage"))
    if not runner.is_available():
        print(f"  ausente ({runner.binary} nao esta no PATH) - coleta seguira sem custo")
    else:
        try:
            print(f"  versao    : {runner.version()}")
            index = CcusageCollector(runner).index()
            print(f"  sessoes   : {len(index)}")
            known = {s.session_id for s in sessions}
            print(f"  casadas   : {len(known & set(index))}")
            print(f"  so no ccusage : {len(set(index) - known)}")
            print(f"  so no transcript: {len(known - set(index))}")
        except CcusageError as exc:
            print(f"  ERRO: {exc}")

    print("== git ==")
    repos = 0
    for project in projects:
        resolve_project_paths(project)
        path = project.primary_path
        if path and find_repository_root(path):
            repos += 1
    print(f"  projetos com repositorio: {repos}/{len(projects)}")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    data_path = _data_path(args)
    for project in discover_projects(data_path):
        resolve_project_paths(project)
        path = project.primary_path
        root = find_repository_root(path) if path else None
        exists = "sim" if path and Path(path).is_dir() else "nao"
        print(f"{project.name}")
        print(f"  key      : {project.project_key}")
        print(f"  path     : {path or 'indeterminado'} (existe: {exists})")
        print(f"  sessoes  : {len(project.session_files)}")
        print(f"  git      : {root or 'nao'}")
        if len(project.cwd_counts) > 1:
            print(f"  outros cwd: {sorted(project.cwd_counts)[:5]}")
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    data_path = _data_path(args)
    sessions = discover_sessions(data_path)
    if args.project:
        sessions = [s for s in sessions if s.project_key == args.project]
    for session in sessions[: args.limit]:
        print(
            f"{session.session_id}  {session.mtime:%Y-%m-%d %H:%M}  "
            f"{session.size_bytes:>10}B  {session.project_key}"
        )
    return 0


def _archived_transcript(session_id: str) -> Path | None:
    """The archived copy of a transcript Claude Code has since removed."""
    root = Path(get_settings().transcript_archive_path)
    if not root.is_dir():
        return None
    matches = sorted(root.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def cmd_report(args: argparse.Namespace) -> int:
    data_path = _data_path(args)
    session_file = find_session_file(data_path, args.session_id)
    if session_file is None:
        # Claude Code prunes its own transcripts. Say which case this is: a
        # session that was never collected reads very differently from one whose
        # source has since been deleted.
        archived = _archived_transcript(args.session_id)
        if archived is not None:
            print(
                f"transcript original ausente; usando a copia arquivada em {archived}",
                file=sys.stderr,
            )
            session_file = SessionFile(
                session_id=args.session_id,
                path=archived,
                project_key=archived.parent.name,
                size_bytes=archived.stat().st_size,
                mtime=datetime.fromtimestamp(archived.stat().st_mtime, tz=timezone.utc),
            )
        else:
            print(
                f"sessao nao encontrada em disco nem no arquivo: {args.session_id}",
                file=sys.stderr,
            )
            return 1

    aggregator = SessionAggregator(
        enable_ccusage=not args.no_ccusage, enable_git=not args.no_git
    )
    report = aggregator.build(session_file)

    if args.json:
        payload = report.model_dump(mode="json", exclude_none=False)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        print(text)
    else:
        print(render_text_report(report))

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"session-{report.session.claude_session_id}.json"
        target.write_text(
            json.dumps(
                report.model_dump(mode="json", exclude_none=False),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\n[relatorio salvo em {target}]", file=sys.stderr)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Incremental synchronisation: only new or changed transcripts."""
    data_path = _data_path(args)
    with session_scope() as db:
        service = SyncService(db, claude_data_path=data_path, force=args.full)
        if args.dry_run:
            from app.collectors.claude.discovery import discover_sessions

            files = discover_sessions(data_path)
            if args.project:
                files = [f for f in files if f.project_key == args.project]
            if args.limit:
                files = files[: args.limit]
            counts: dict[str, int] = {}
            for session_file in files:
                decision, _ = service.decide(session_file)
                counts[decision.value] = counts.get(decision.value, 0) + 1
                if decision not in (
                    SyncDecision.SKIPPED_UNCHANGED,
                    SyncDecision.EMPTY,
                ):
                    print(f"  {decision.value:<18} {session_file.session_id}")
            print("\n-- dry run, nada foi gravado --")
            for name, count in sorted(counts.items()):
                print(f"  {name:<18} {count}")
            return 0

        summary = service.sync_all(project_key=args.project, limit=args.limit)
        pruned = summary.pruned_projects
        archived = summary.archived_files
        archived_bytes = summary.archived_bytes
        archive_errors = summary.archive_errors

    elapsed = (summary.finished_at - summary.started_at).total_seconds()
    print(f"sincronizacao concluida em {elapsed:.1f}s")
    for decision in SyncDecision:
        count = summary.count(decision)
        if count:
            print(f"  {decision.value:<18} {count}")
    print(f"  {'processadas':<18} {summary.processed}")
    if pruned:
        print(f"  {'projetos removidos':<18} {pruned} (ficaram sem sessoes)")
    if archived:
        print(
            f"  {'arquivados':<18} {archived} transcript(s), "
            f"{archived_bytes / 1_048_576:.1f} MB"
        )
    for failure in archive_errors:
        print(f"  AVISO arquivamento: {failure}", file=sys.stderr)
    for failure in summary.failures:
        print(f"  FALHA {failure.session_id}: {failure.error}", file=sys.stderr)
    return 1 if summary.failures else 0


def cmd_db_projects(args: argparse.Namespace) -> int:
    with session_scope() as db:
        rows = db.scalars(select(Project).order_by(Project.name)).all()
        if not rows:
            print("nenhum projeto sincronizado - rode 'sync' primeiro")
            return 0
        for project in rows:
            session_count = db.scalar(
                select(func.count(Session.id)).where(Session.project_id == project.id)
            )
            cost = db.scalar(
                select(func.sum(Usage.cost_usd))
                .join(Session, Session.id == Usage.session_id)
                .where(
                    Session.project_id == project.id,
                    Usage.source == "ccusage",
                    Usage.model.is_(None),
                )
            )
            tokens = db.scalar(
                select(func.sum(Usage.total_tokens))
                .join(Session, Session.id == Usage.session_id)
                .where(
                    Session.project_id == project.id,
                    Usage.source == "ccusage",
                    Usage.model.is_(None),
                )
            )
            cost_text = f"US$ {cost:.2f}" if cost is not None else "n/d"
            print(
                f"[{project.id:>3}] {project.name:<28} sessoes={session_count:<4} "
                f"tokens={tokens or 0:>12}  custo={cost_text}"
            )
            print(f"      {project.path}")
    return 0


def cmd_db_sessions(args: argparse.Namespace) -> int:
    with session_scope() as db:
        query = select(Session).order_by(Session.started_at.desc().nulls_last())
        if args.project_id:
            query = query.where(Session.project_id == args.project_id)
        for row in db.scalars(query.limit(args.limit)).all():
            project = db.get(Project, row.project_id) if row.project_id else None
            started = row.started_at.strftime("%Y-%m-%d %H:%M") if row.started_at else "?"
            turns = f"{(row.active_turn_seconds or 0) / 60:.0f}m"
            print(
                f"{row.claude_session_id}  {started}  turnos={turns:<7} "
                f"{row.status:<11} prompts={row.human_prompt_count:<4} "
                f"tools={row.tool_call_count:<5} {(project.name if project else '-')}"
            )
            if row.title:
                print(f"    {row.title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with session_scope() as db:
        row = db.scalar(
            select(Session).where(Session.claude_session_id == args.session_id)
        )
        if row is None:
            print(f"sessao nao sincronizada: {args.session_id}", file=sys.stderr)
            return 1
        project = db.get(Project, row.project_id) if row.project_id else None
        print(f"SESSION {row.claude_session_id}   [db id {row.id}]")
        if row.title:
            print(f'"{row.title}"')
        print(f"Projeto: {project.name if project else 'unknown'}")
        print(f"Status : {row.status}")
        print(
            f"Janela : {row.started_at} -> {row.ended_at} "
            f"(turnos ativos: {(row.active_turn_seconds or 0) / 60:.0f} min)"
        )
        print(
            f"Contagens: prompts={row.human_prompt_count} "
            f"(confirmados={row.confirmed_human_prompt_count}) "
            f"mensagens={row.assistant_message_count} tools={row.tool_call_count} "
            f"erros={row.tool_error_count}"
        )
        print(
            f"Arquivos : {row.files_touched} tocados, "
            f"{row.files_modified} modificados, "
            f"{row.files_outside_project} fora do projeto"
        )

        print("\nTOKENS (totais por fonte)")
        totals = db.scalars(
            select(Usage).where(Usage.session_id == row.id, Usage.model.is_(None))
        ).all()
        for usage in totals:
            cost = f"US$ {usage.cost_usd:.4f}" if usage.cost_usd is not None else "n/d"
            print(f"  {usage.source:>18}: {usage.total_tokens:>12} tokens  custo={cost}")

        print("\nCOMMITS ASSOCIADOS")
        links = db.scalars(
            select(SessionCommitLink)
            .where(SessionCommitLink.session_id == row.id)
            .order_by(SessionCommitLink.likely_related.desc())
        ).all()
        if not links:
            print("  nenhum")
        for link in links:
            commit = db.get(GitCommit, link.git_commit_id)
            flag = "LIKELY RELATED" if link.likely_related else "sem evidencia"
            print(f"  {commit.commit_hash[:10]}  [{flag}]  {commit.message[:60]}")
            for reason in link.reasons:
                print(f"      - {reason}")

        if row.collection_warnings:
            print("\nAVISOS DA COLETA")
            for warning in row.collection_warnings:
                print(f"  - {warning}")

        source = row.source_file
        if source:
            print("\nPROVENIENCIA")
            print(f"  {source.transcript_path}")
            print(f"  sha256 {source.transcript_sha256}")
    return 0


def _print_analysis(analysis: AIAnalysis) -> None:
    print(f"  tipo        : {analysis.task_type}")
    print(f"  complexidade: {analysis.complexity}")
    print(f"  sucesso     : {analysis.success if analysis.success is not None else 'indeterminado'}")
    print(f"  retrabalho  : {analysis.requires_rework}")
    for evidence in analysis.rework_evidence:
        print(f"      - {evidence}")
    print(f"  confianca   : {analysis.confidence}")
    print(f"  objetivo    : {analysis.objective}")
    print(f"  resumo      : {analysis.summary}")
    if analysis.result:
        print(f"  resultado   : {analysis.result}")
    if analysis.technologies:
        print(f"  tecnologias : {', '.join(analysis.technologies)}")
    for problem in analysis.problems_encountered:
        print(f"  problema    : {problem}")


def cmd_analyze(args: argparse.Namespace) -> int:
    """Run the LLM analysis. Sends nothing unless AI_ANALYSIS_ENABLED is true."""
    settings = get_settings()
    with session_scope() as db:
        analyzer = SessionAnalyzer(db)

        if args.all:
            targets = analyzer.unanalysed_sessions(limit=args.limit)
        else:
            row = db.scalar(
                select(Session).where(Session.claude_session_id == args.session_id)
            )
            if row is None:
                print(f"sessao nao sincronizada: {args.session_id}", file=sys.stderr)
                return 1
            targets = [row]

        if not targets:
            print("nada a analisar")
            return 0

        if args.dry_run:
            # Show exactly what would leave the machine, before anyone agrees
            # to send it.
            for row in targets:
                digest, redaction = analyzer.prepare(row)
                print(f"=== {row.claude_session_id} ===")
                print(f"digest: {digest.chars} caracteres | {redaction.describe()}")
                if digest.truncations:
                    print(f"omitido: {'; '.join(digest.truncations)}")
                if args.show_digest:
                    print("-" * 60)
                    print(digest.text)
                    print("-" * 60)
            print(f"\n-- dry run: nada foi enviado ({len(targets)} sessao(oes)) --")
            return 0

        if not settings.ai_analysis_enabled:
            print(
                "AI_ANALYSIS_ENABLED=false - nenhum dado foi enviado.\n"
                "Para habilitar: defina AI_ANALYSIS_ENABLED=true e LLM_API_KEY no .env.\n"
                "Use 'analyze --dry-run --show-digest' para ver antes o que sairia da maquina.",
                file=sys.stderr,
            )
            return 2

        failures = 0
        for row in targets:
            print(f"=== {row.claude_session_id} ===")
            try:
                outcome = analyzer.analyze(row)
            except (AnalysisDisabled, LLMNotConfigured) as exc:
                print(f"  ERRO: {exc}", file=sys.stderr)
                return 2
            if not outcome.ok:
                failures += 1
                print(f"  FALHA: {outcome.error}", file=sys.stderr)
                continue
            print(
                f"  provider={outcome.provider} model={outcome.model} "
                f"tokens_in={outcome.input_tokens} tokens_out={outcome.output_tokens} "
                f"| {outcome.redaction.describe()}"
            )
            _print_analysis(outcome.analysis)
        return 1 if failures else 0


def cmd_survey(args: argparse.Namespace) -> int:
    """Record the developer's own view of a session.

    Perceived quality is not in any transcript; it needs an instrument. Answers
    are stored in their own table so an analysis can always tell declared data
    apart from observed and inferred.
    """
    fields = {
        "perceived_quality": args.quality,
        "perceived_productivity": args.productivity,
        "perceived_effort": args.effort,
        "trust_in_output": args.trust,
        "rework_reported": args.rework,
        "respondent": args.respondent,
        "comment": args.comment,
    }
    provided = {k: v for k, v in fields.items() if v is not None}
    if not provided:
        print(
            "informe ao menos uma resposta, por exemplo:\n"
            "  survey <ID> --quality 4 --productivity 5 --effort 2 --trust 4",
            file=sys.stderr,
        )
        return 2

    with session_scope() as db:
        row = db.scalar(
            select(Session).where(Session.claude_session_id == args.session_id)
        )
        if row is None:
            print(f"sessao nao sincronizada: {args.session_id}", file=sys.stderr)
            return 1
        survey = db.scalar(
            select(SessionSurvey).where(SessionSurvey.session_id == row.id)
        )
        replacing = survey is not None
        if survey is None:
            survey = SessionSurvey(session_id=row.id)
            db.add(survey)
        for field, value in provided.items():
            setattr(survey, field, value)
        db.flush()

        print(f"{'atualizada' if replacing else 'registrada'}: {row.claude_session_id}")
        for label, value in (
            ("qualidade percebida", survey.perceived_quality),
            ("produtividade percebida", survey.perceived_productivity),
            ("esforco percebido", survey.perceived_effort),
            ("confianca na saida", survey.trust_in_output),
        ):
            print(f"  {label:<26} {value if value is not None else 'nao respondido'}")
        print(
            f"  {'retrabalho declarado':<26} "
            f"{survey.rework_reported if survey.rework_reported is not None else 'nao respondido'}"
        )
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Compute git activity before and after AI adoption."""
    adoption = None
    if args.adoption:
        try:
            adoption = datetime.fromisoformat(args.adoption).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"data invalida: {args.adoption} (use YYYY-MM-DD)", file=sys.stderr)
            return 2

    with session_scope() as db:
        if args.project_id:
            projects = [db.get(ProjectRow, args.project_id)]
            if projects[0] is None:
                print(f"projeto {args.project_id} nao encontrado", file=sys.stderr)
                return 1
        else:
            projects = list(
                db.scalars(
                    select(ProjectRow).where(ProjectRow.is_git_repository.is_(True))
                ).all()
            )
        if not projects:
            print("nenhum projeto com repositorio git")
            return 0

        failures = 0
        for project in projects:
            try:
                row = compute_baseline(
                    db, project, adoption_at=adoption, window_days=args.days
                )
            except BaselineError as exc:
                failures += 1
                print(f"{project.name}: {exc}", file=sys.stderr)
                continue
            rates = baseline_rates(row)
            print(f"=== {project.name} ===")
            print(
                f"  adocao {row.adoption_at:%Y-%m-%d} · janelas de {row.window_days} dias"
            )
            print(
                f"  antes : {row.before_commits:>4} commits  +{row.before_insertions:<7}"
                f" {row.before_active_days} dia(s) ativo(s)  {len(row.before_authors)} autor(es)"
            )
            print(
                f"  depois: {row.after_commits:>4} commits  +{row.after_insertions:<7}"
                f" {row.after_active_days} dia(s) ativo(s)  {len(row.after_authors)} autor(es)"
            )
            before_rate = rates["before_commits_per_active_day"]
            after_rate = rates["after_commits_per_active_day"]
            print(
                "  commits por dia ativo: "
                f"{before_rate:.2f} -> {after_rate:.2f}"
                if before_rate is not None and after_rate is not None
                else "  commits por dia ativo: indeterminado"
            )
            for note in row.notes:
                print(f"  ! {note}")
        return 1 if failures == len(projects) else 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Projects side by side, normalised by effort."""
    with session_scope() as db:
        rows = analytics_service.project_comparison(db)
    if not rows:
        print("nenhum projeto sincronizado")
        return 0

    header = (
        f"{'projeto':<28} {'sess':>5} {'horas':>7} {'prm/h':>7} "
        f"{'US$/h':>8} {'commits':>8} {'atrib':>7} {'cobert':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        def pct(value):
            return f"{value * 100:.0f}%" if value is not None else "n/d"

        def num(value, fmt="{:.1f}"):
            return fmt.format(value) if value is not None else "n/d"

        print(
            f"{row['name'][:28]:<28} {row['sessions']:>5} "
            f"{row['active_turn_hours']:>7.1f} {num(row['prompts_per_hour']):>7} "
            f"{num(row['cost_per_hour'], '{:.2f}'):>8} {row['linked_commits']:>8} "
            f"{pct(row['attribution_ratio']):>7} {pct(row['commit_coverage']):>7}"
        )
    print(
        "\natrib = teto do que passou por edicao rastreada da IA; "
        "cobert = sessoes com commit associado"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-analytics")
    parser.add_argument(
        "--claude-data-path",
        default=None,
        help="Claude Code data directory (default: auto-detect)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="inspeciona o ambiente").set_defaults(func=cmd_doctor)
    sub.add_parser("projects", help="lista projetos").set_defaults(func=cmd_projects)

    sessions = sub.add_parser("sessions", help="lista sessoes")
    sessions.add_argument("--project", default=None)
    sessions.add_argument("--limit", type=int, default=30)
    sessions.set_defaults(func=cmd_sessions)

    report = sub.add_parser("report", help="gera o relatorio de uma sessao")
    report.add_argument("session_id")
    report.add_argument("--json", action="store_true")
    report.add_argument("--out", default=None, help="diretorio para salvar o JSON")
    report.add_argument("--no-ccusage", action="store_true")
    report.add_argument("--no-git", action="store_true")
    report.set_defaults(func=cmd_report)

    sync = sub.add_parser("sync", help="sincroniza transcripts novos/alterados")
    sync.add_argument("--project", default=None, help="chave do projeto no Claude Code")
    sync.add_argument("--limit", type=int, default=None)
    sync.add_argument(
        "--dry-run", action="store_true", help="mostra o que seria processado"
    )
    sync.add_argument(
        "--full",
        action="store_true",
        help="reprocessa tudo, ignorando o estado de sincronizacao",
    )
    sync.set_defaults(func=cmd_sync)

    sub.add_parser("db-projects", help="projetos ja persistidos").set_defaults(
        func=cmd_db_projects
    )

    db_sessions = sub.add_parser("db-sessions", help="sessoes ja persistidas")
    db_sessions.add_argument("--limit", type=int, default=20)
    db_sessions.add_argument("--project-id", type=int, default=None)
    db_sessions.set_defaults(func=cmd_db_sessions)

    show = sub.add_parser("show", help="detalhe de uma sessao persistida")
    show.add_argument("session_id")
    show.set_defaults(func=cmd_show)

    analyze = sub.add_parser("analyze", help="analise por LLM (dado inferido)")
    analyze.add_argument("session_id", nargs="?", default=None)
    analyze.add_argument(
        "--all", action="store_true", help="todas as sessoes ainda sem analise"
    )
    analyze.add_argument("--limit", type=int, default=None)
    analyze.add_argument(
        "--dry-run",
        action="store_true",
        help="monta e redige o digest sem enviar nada",
    )
    analyze.add_argument(
        "--show-digest", action="store_true", help="imprime o digest inteiro"
    )
    analyze.set_defaults(func=cmd_analyze)

    survey = sub.add_parser("survey", help="registra a percepcao do desenvolvedor")
    survey.add_argument("session_id")
    survey.add_argument("--quality", type=int, choices=range(1, 6), default=None,
                        help="qualidade percebida do resultado (1 ruim - 5 otima)")
    survey.add_argument("--productivity", type=int, choices=range(1, 6), default=None,
                        help="produtividade percebida (1 baixa - 5 alta)")
    survey.add_argument("--effort", type=int, choices=range(1, 6), default=None,
                        help="esforco mental percebido (1 leve - 5 pesado)")
    survey.add_argument("--trust", type=int, choices=range(1, 6), default=None,
                        help="confianca na saida (1 nenhuma - 5 total)")
    survey.add_argument("--rework", action=argparse.BooleanOptionalAction, default=None,
                        help="precisou refazer trabalho nesta sessao")
    survey.add_argument("--respondent", default=None)
    survey.add_argument("--comment", default=None)
    survey.set_defaults(func=cmd_survey)

    baseline = sub.add_parser("baseline", help="compara o git antes e depois da adocao")
    baseline.add_argument("--project-id", type=int, default=None)
    baseline.add_argument("--adoption", default=None,
                          help="data de adocao (YYYY-MM-DD); padrao: primeira sessao")
    baseline.add_argument("--days", type=int, default=90, help="tamanho das janelas")
    baseline.set_defaults(func=cmd_baseline)

    sub.add_parser("compare", help="projetos lado a lado").set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
