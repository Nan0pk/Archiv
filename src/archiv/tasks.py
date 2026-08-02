"""Bounded deterministic task execution and independent run verification."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from archiv.contracts import RetrievalDiagnostics, RunStatus, SearchResult
from archiv.grounding import build_grounding_prompt, parse_and_validate_grounded_response
from archiv.hashing import sha256_file
from archiv.model_adapter import build_model_adapter, load_model_config
from archiv.report_contracts import ReportManifest, ReportStatus
from archiv.reports import generate_report, generate_report_from_results, validate_report
from archiv.reports.validation import write_validation
from archiv.search import rebuild_search_index, retrieve_evidence
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout
from archiv.task_contracts import CrossFileReportTask, TaskRunResult, TaskVerification

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def load_task(path: Path) -> CrossFileReportTask:
    """Load JSON-compatible YAML without adding a second parser dependency."""

    path = path.expanduser().resolve(strict=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            "task files use the JSON subset of YAML; expected a JSON object"
        ) from error
    return CrossFileReportTask.model_validate(payload)


def _source_hashes(layout: ArchivLayout) -> dict[str, str]:
    database = ArchivDatabase(layout.database)
    database.initialize()
    with database.connect() as connection:
        rows = connection.execute("SELECT sha256 FROM objects ORDER BY sha256").fetchall()
    hashes: dict[str, str] = {}
    for row in rows:
        digest = str(row["sha256"])
        original = layout.original_path(digest)
        if not original.is_file():
            raise RuntimeError(f"canonical original is missing: {digest}")
        hashes[digest] = sha256_file(original)
    return hashes


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_task(task_path: Path, *, home: Path | None = None) -> TaskRunResult:
    """Execute one deterministic cross-file report task and preserve terminal evidence."""

    task_path = task_path.expanduser().resolve(strict=True)
    task = load_task(task_path)
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    run_id = uuid4().hex
    evidence_dir = layout.runs / "tasks" / run_id
    output_dir = layout.outputs / "tasks" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)
    model = load_model_config(layout.root)
    _write_json(
        evidence_dir / "request.json",
        {
            "schema_version": "1",
            "run_id": run_id,
            "task_path": str(task_path),
            "task": task.model_dump(mode="json"),
            "model": model.model_dump(mode="json"),
            "network_policy": "denied",
        },
    )

    output_name = Path(task.output_name)
    if output_name.name != task.output_name or output_name.suffix.lower() != ".docx":
        error = "task output_name must be a simple .docx basename"
        result = TaskRunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            task_path=str(task_path),
            evidence_dir=str(evidence_dir),
            model=model,
            errors=[error],
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result
    if task.model_policy == "configured-local" and model.adapter == "disabled":
        error = "task requires a configured local model; hidden fallback is forbidden"
        result = TaskRunResult(
            run_id=run_id,
            status=RunStatus.BLOCKED_BY_POLICY,
            task_path=str(task_path),
            evidence_dir=str(evidence_dir),
            model=model,
            errors=[error],
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result

    before: dict[str, str] = {}
    retrieval_results: list[SearchResult] | None = None
    retrieval_diagnostics: RetrievalDiagnostics | None = None
    try:
        before = _source_hashes(layout)
        if not before:
            raise ValueError("task requires at least one ingested source")
        rebuild_search_index(home=layout.root)
        output = output_dir / task.output_name

        grounded_response = None
        model_identity = (
            model.adapter if model.adapter == "disabled" else f"{model.adapter} ({model.model})"
        )

        if task.model_policy == "configured-local" and model.adapter != "disabled":
            retrieval = retrieve_evidence(
                task.query,
                home=layout.root,
                evidence_limit=task.max_sources,
            )
            retrieval_results = retrieval.results
            retrieval_diagnostics = retrieval.diagnostics
            _write_json(
                evidence_dir / "retrieval.json",
                retrieval.diagnostics.model_dump(mode="json"),
            )
            citations_map = {
                f"CIT-{index}": result
                for index, result in enumerate(retrieval.results, start=1)
            }

            if citations_map:
                prompt = build_grounding_prompt(task.query, citations_map)
                adapter = build_model_adapter(model)
                raw_response = adapter.complete(prompt)
                parsed, p_errors = parse_and_validate_grounded_response(
                    raw_response, set(citations_map.keys())
                )
                if p_errors or parsed is None:
                    raise ValueError("model response validation failed: " + "; ".join(p_errors))
                grounded_response = parsed

        if retrieval_results is None:
            report = generate_report(
                task.query,
                output,
                title=task.title,
                home=layout.root,
                max_sources=task.max_sources,
                render=task.render,
                evidence_dir=output_dir / "rendered",
                grounded_response=grounded_response,
                model_identity=model_identity,
            )
        else:
            report = generate_report_from_results(
                retrieval_results,
                output,
                query=task.query,
                title=task.title,
                home=layout.root,
                max_sources=task.max_sources,
                render=task.render,
                evidence_dir=output_dir / "rendered",
                grounded_response=grounded_response,
                model_identity=model_identity,
            )
        after = _source_hashes(layout)
        manifest = ReportManifest.model_validate_json(
            Path(report.manifest_path).read_text(encoding="utf-8")
        )
        errors: list[str] = []
        if before != after:
            errors.append("canonical source hashes changed during task execution")
        if report.status is not ReportStatus.SUCCEEDED or not report.validation.valid:
            errors.extend(report.validation.errors or ["report validation failed"])
        status = RunStatus.SUCCEEDED if not errors else RunStatus.PARTIALLY_PRODUCED_BUT_INVALID
        result = TaskRunResult(
            run_id=run_id,
            status=status,
            task_path=str(task_path),
            evidence_dir=str(evidence_dir),
            output_path=report.docx_path,
            manifest_path=report.manifest_path,
            validation_path=report.validation_path,
            source_hashes_before=before,
            source_hashes_after=after,
            citation_count=len(manifest.sources),
            retrieval_diagnostics=retrieval_diagnostics,
            model=model,
            errors=errors,
        )
    except Exception as error:
        after = _source_hashes(layout) if before else {}
        result = TaskRunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            task_path=str(task_path),
            evidence_dir=str(evidence_dir),
            source_hashes_before=before,
            source_hashes_after=after,
            retrieval_diagnostics=retrieval_diagnostics,
            model=model,
            errors=[f"{type(error).__name__}: {error}"],
        )
    _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
    return result


def verify_task_run(
    run_id: str,
    *,
    home: Path | None = None,
    render: bool | None = None,
) -> TaskVerification:
    """Re-open one task run and independently revalidate its sources and report."""

    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be 32 lowercase hexadecimal characters")
    layout = ArchivLayout.resolve(home)
    evidence_dir = layout.runs / "tasks" / run_id
    result_path = evidence_dir / "result.json"
    if not result_path.is_file():
        raise FileNotFoundError(f"unknown task run: {run_id}")
    result = TaskRunResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    current = _source_hashes(layout)
    source_hashes_match = current == result.source_hashes_before == result.source_hashes_after
    if not source_hashes_match:
        errors.append("canonical source hashes do not match the task evidence")
    report_validation = None
    if not result.output_path or not result.manifest_path:
        errors.append("task result has no report artifact to verify")
    else:
        output = Path(result.output_path).resolve()
        manifest = Path(result.manifest_path).resolve()
        allowed = (layout.outputs / "tasks" / run_id).resolve()
        if not output.is_relative_to(allowed) or not manifest.is_relative_to(allowed):
            errors.append("task report paths escape the run output directory")
        else:
            request_path = evidence_dir / "request.json"
            if not request_path.is_file():
                errors.append("task request evidence is missing")
                task = CrossFileReportTask(query="missing")
            else:
                request = json.loads(request_path.read_text(encoding="utf-8"))
                task = CrossFileReportTask.model_validate(request["task"])
            should_render = task.render if render is None else render
            report_validation = validate_report(
                output,
                manifest,
                home=layout.root,
                render=should_render,
                evidence_dir=allowed / "verification-rendered",
            )
            if not report_validation.valid:
                errors.extend(report_validation.errors)
            write_validation(evidence_dir / "verification.json", report_validation)
    verification = TaskVerification(
        run_id=run_id,
        valid=not errors and result.status is RunStatus.SUCCEEDED,
        errors=errors,
        source_hashes_match=source_hashes_match,
        report_validation=report_validation,
    )
    _write_json(evidence_dir / "verification-result.json", verification.model_dump(mode="json"))
    return verification
