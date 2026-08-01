"""Public and private field-trial orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from archiv.search import search_documents
from field_trial.common import (
    PRIVATE_KEYS,
    PRIVATE_ROOT,
    SCHEMA_VERSION,
    SUPPORTED_SUFFIXES,
    load_benchmark,
    sha256_file,
)
from field_trial.fixtures import FakeModelServer, _source_maps, generate_public_corpus
from field_trial.scoring import (
    _aggregate,
    _copy_report_artifacts,
    _defects,
    _facts_exist,
    _failure_category,
    _json_output,
    _markdown,
    _normalized_text,
    calculate_retrieval_metrics,
    run_command,
    scan_safe_artifacts,
    score_answer,
    validate_structural_citations,
)


def run_public_trial(args: argparse.Namespace) -> dict[str, object]:
    benchmark = load_benchmark(args.benchmark)
    output = args.output.resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    workspace = Path(tempfile.mkdtemp(prefix="archiv-public-field-trial-"))
    corpus = workspace / "corpus"
    home = workspace / "archiv-home"
    manifest = generate_public_corpus(benchmark, corpus)
    before = {str(item["filename"]): str(item["sha256"]) for item in manifest}
    source_files, by_filename = _source_maps(benchmark)
    command = args.archiv_command.split()
    add = run_command([*command, "add", str(corpus), "--home", str(home), "--json"])
    if add["returncode"] != 0:
        raise RuntimeError(f"public indexing failed: {add['stderr']}")
    results: list[dict[str, object]] = []
    with FakeModelServer(benchmark) as server:
        configure = run_command(
            [
                *command,
                "model",
                "configure",
                "--endpoint",
                server.endpoint,
                "--model",
                "archiv-field-trial-fixture",
                "--timeout",
                "10",
                "--home",
                str(home),
                "--json",
            ]
        )
        if configure["returncode"] != 0:
            raise RuntimeError(f"fake model configuration failed: {configure['stderr']}")
        model_test = run_command([*command, "model", "test", "--home", str(home), "--json"])
        if model_test["returncode"] != 0:
            raise RuntimeError(f"fake model test failed: {model_test['stderr']}")
        normalized = _normalized_text(home)
        limit = int(benchmark["evidence_limit"])
        for question in cast(Sequence[Mapping[str, object]], benchmark["questions"]):
            text = str(question["question"])
            retrieved = search_documents(text, home=home, limit=limit * 2)
            selected: list[Any] = []
            seen: set[str] = set()
            for item in retrieved:
                if item.citation.object_sha256 not in seen:
                    selected.append(item)
                    seen.add(item.citation.object_sha256)
                if len(selected) == limit:
                    break
            source_ids = [
                by_filename.get(item.citation.source_name, "UNKNOWN") for item in selected
            ]
            retrieval = calculate_retrieval_metrics(
                cast(Sequence[str], question["expected_sources"]), source_ids, limit
            )
            retrieval.update(
                {
                    "derived_query": text,
                    "query_strategy": "single exact FTS phrase",
                    "retrieved_passage_count": len(retrieved),
                    "selected_passage_count": len(selected),
                    "retrieval_ranks": [round(float(item.rank), 6) for item in selected],
                    "normalized_evidence_contains_required_facts": _facts_exist(
                        question, normalized, source_files
                    ),
                    "duplicate_passage_count": len(selected)
                    - len({hashlib.sha256(item.text.encode()).hexdigest() for item in selected}),
                }
            )
            ask = run_command(
                [
                    *command,
                    "ask",
                    text,
                    "--home",
                    str(home),
                    "--max-sources",
                    str(limit),
                    "--json",
                ]
            )
            payload = _json_output(ask)
            grounded = payload.get("grounded_response")
            response = grounded if isinstance(grounded, dict) else None
            citations_raw = payload.get("retrieved_citations", [])
            count = len(citations_raw) if isinstance(citations_raw, list) else 0
            citations = validate_structural_citations(
                response, {f"CIT-{index}" for index in range(1, count + 1)}
            )
            answer = score_answer(question, response)
            results.append(
                {
                    "question_id": question["id"],
                    "category": question["category"],
                    "status": payload.get("status"),
                    "retrieval": retrieval,
                    "citation_integrity": citations,
                    "answer_quality": answer,
                    "duration_ms": ask["duration_ms"],
                    "failure_category": _failure_category(retrieval, citations, answer),
                }
            )
        report = run_command(
            [
                *command,
                "report",
                "Project status evidence",
                "--title",
                "Archiv Public Field-Trial Evidence Report",
                "--deterministic",
                "--home",
                str(home),
                "--json",
                "--render" if args.render_report else "--no-render",
            ]
        )
        report_payload = _json_output(report) if report["returncode"] == 0 else {"status": "failed"}
    after = {path.name: sha256_file(path) for path in corpus.iterdir() if path.is_file()}
    aggregate = _aggregate(results, float(add["duration_ms"]))
    artifacts = _copy_report_artifacts(report_payload, home, output)
    summary: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "public",
        "benchmark_id": benchmark["benchmark_id"],
        "corpus_document_count": len(manifest),
        "question_count": len(results),
        "evidence_limit": benchmark["evidence_limit"],
        "model": {
            "adapter": "openai-compatible-loopback",
            "identity": "archiv-field-trial-fixture",
            "purpose": "isolate retrieval and deterministic validators",
        },
        "aggregate": aggregate,
        "defects": _defects(aggregate),
        "source_navigation": {
            "source_name_available": True,
            "native_locator_available": True,
            "safe_open_command_available": False,
        },
        "report": {
            "status": report_payload.get("status", "unknown"),
            "artifacts": artifacts,
            "visual_inspection": "pending manual inspection of rendered pages",
        },
        "privacy": {
            "public_safe_fixture_only": True,
            "original_hashes_unchanged": before == after,
            "private_data_present": False,
        },
        "known_limitations": [
            "The deterministic fixture model does not measure real-model synthesis quality.",
            "Private local results are intentionally excluded from public artifacts.",
        ],
        "questions": results,
    }
    (output / "corpus-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "public-results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "public-report.md").write_text(_markdown(summary), encoding="utf-8")
    safety = scan_safe_artifacts(output, [str(workspace), str(Path.home())])
    if safety:
        raise RuntimeError("public artifact safety scan failed: " + "; ".join(safety))
    shutil.rmtree(workspace)
    return summary


def redact_private(value: object, private_values: Iterable[str] = ()) -> object:
    forbidden = [item for item in private_values if item]
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key.casefold() in PRIVATE_KEYS
            else redact_private(child, forbidden)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_private(item, forbidden) for item in value]
    if isinstance(value, str):
        result = value
        for item in forbidden:
            result = result.replace(item, "[redacted]")
        return re.sub(r"/(home|Users)/[^/\s]+/[^\s]*", "[redacted-path]", result)
    return value


def validate_private_request(corpus: Path | None, local_only: bool) -> None:
    if corpus is not None and not local_only:
        raise ValueError("private corpus processing requires explicit --local-only opt-in")


def _copy_private_corpus(source: Path, destination: Path) -> tuple[list[Path], dict[str, str]]:
    candidates = [source] if source.is_file() else sorted(source.rglob("*"))
    files = [
        path
        for path in candidates
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
    ]
    if not files:
        raise ValueError("no supported files found in selected private corpus")
    destination.mkdir(parents=True)
    hashes: dict[str, str] = {}
    copied: list[Path] = []
    for index, path in enumerate(files, start=1):
        hashes[str(path)] = sha256_file(path)
        target = destination / f"source-{index:04d}{path.suffix.casefold()}"
        shutil.copy2(path, target)
        copied.append(target)
    return copied, hashes


def _private_questions(path: Path | None) -> list[str]:
    if path is None:
        return [
            "What decisions have been made about the project architecture?",
            "Which decisions remain unresolved?",
            "What work is described as complete?",
            "What remains unfinished?",
            "Which documents contradict one another?",
            "Which source appears to contain the latest approved position?",
            "What dates or deadlines are established?",
            "Which numerical claims appear, and where?",
            "What risks are explicitly documented?",
            "What assumptions are repeated without supporting evidence?",
            "What is not established by the available documents?",
            "Which actions have named owners?",
            "Which actions lack owners?",
            "Which statements are marked as superseded?",
            "Which documents appear duplicated?",
            "What evidence supports the current status?",
            "What evidence is missing for key claims?",
            "Which source is most recent?",
            "What should be independently verified?",
            (
                "Prepare a cited current-status report separating completed work, unresolved "
                "decisions, risks, contradictions and missing evidence."
            ),
        ]
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return cast(list[str], value)
    raise ValueError("private questions must be a JSON list of non-empty strings")


def run_private_trial(args: argparse.Namespace) -> dict[str, object]:
    validate_private_request(args.corpus, args.local_only)
    if args.corpus is None:
        raise ValueError("private mode requires --corpus")
    corpus = args.corpus.resolve()
    if not corpus.exists():
        raise ValueError("selected private corpus does not exist")
    output = (
        args.output.resolve()
        if args.output
        else PRIVATE_ROOT.resolve() / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    disposable = output / "disposable-corpus"
    home = output / "archiv-home"
    copied, hashes = _copy_private_corpus(corpus, disposable)
    questions = _private_questions(args.questions)
    command = args.archiv_command.split()
    add = run_command([*command, "add", str(disposable), "--home", str(home), "--json"])
    if add["returncode"] != 0:
        raise RuntimeError("private corpus indexing failed; inspect local details")
    if bool(args.model_endpoint) != bool(args.model_name):
        raise ValueError("--model-endpoint and --model-name must be provided together")
    model_available = bool(args.model_endpoint and args.model_name)
    if model_available:
        configured = run_command(
            [
                *command,
                "model",
                "configure",
                "--endpoint",
                args.model_endpoint,
                "--model",
                args.model_name,
                "--home",
                str(home),
                "--json",
            ]
        )
        if configured["returncode"] != 0:
            raise RuntimeError("explicit local model configuration failed")
    details: list[dict[str, object]] = []
    durations: list[float] = []
    for question in questions:
        retrieved = search_documents(question, home=home, limit=args.evidence_limit * 2)
        item: dict[str, object] = {
            "question": question,
            "retrieved_passages": len(retrieved),
            "source_names": [
                result.citation.source_name for result in retrieved[: args.evidence_limit]
            ],
        }
        if model_available:
            ask = run_command(
                [
                    *command,
                    "ask",
                    question,
                    "--home",
                    str(home),
                    "--max-sources",
                    str(args.evidence_limit),
                    "--json",
                ]
            )
            item["ask"] = _json_output(ask) if ask["stdout"] else {"status": "failed"}
            item["duration_ms"] = ask["duration_ms"]
            durations.append(float(ask["duration_ms"]))
        details.append(item)
    unchanged = all(sha256_file(Path(path)) == digest for path, digest in hashes.items())
    (output / "private-details.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "private-local",
                "corpus_path": str(corpus),
                "questions": details,
                "model_name": args.model_name,
                "source_hashes_unchanged": unchanged,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "mode": "private-local-sanitized",
        "question_count": len(questions),
        "corpus_file_count": len(copied),
        "model_available": model_available,
        "median_question_duration_ms": (
            sorted(durations)[len(durations) // 2] if durations else None
        ),
        "source_hashes_unchanged": unchanged,
        "external_blocker": (
            None
            if model_available
            else (
                "No explicit suitable local model endpoint was supplied; "
                "answer-quality testing was not attempted."
            )
        ),
        "privacy": {
            "filenames_included": False,
            "paths_included": False,
            "questions_included": False,
            "excerpts_included": False,
        },
    }
    sanitized = cast(dict[str, object], redact_private(summary, [str(corpus), *hashes]))
    (output / "sanitized-summary.json").write_text(
        json.dumps(sanitized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return sanitized
