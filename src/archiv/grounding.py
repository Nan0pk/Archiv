"""Grounding protocol, evidence packaging, model response validation, and ask workflow."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from archiv.ask_contracts import AskRunResult
from archiv.contracts import Citation, RunStatus, SearchResult
from archiv.evaluation_config import EvaluationNotEnabledError, check_evaluation_opt_in
from archiv.grounding_contracts import GroundedModelResponse
from archiv.model_adapter import build_model_adapter, load_model_config
from archiv.reports.formatting import format_locator
from archiv.search import read_source_excerpt, retrieve_evidence, validate_citation
from archiv.storage.layout import ArchivLayout
from archiv.structured_output import request_grounded_response


def build_grounding_prompt(query: str, citations: dict[str, SearchResult]) -> str:
    """Construct bounded evidence package and task instructions for the local model."""

    evidence_lines: list[str] = []
    for citation_id, result in citations.items():
        locator_str = format_locator(result.citation.locator)
        evidence_lines.append(
            f"[{citation_id}] Source: {result.citation.source_name} ({locator_str})\n"
            f"Excerpt: {result.text}"
        )

    evidence_text = "\n\n".join(evidence_lines) or "No relevant evidence found."
    allowed_list = ", ".join(sorted(citations.keys())) or "None"

    return f"""You are Archiv's grounded evidence synthesis assistant.
Your task is to answer the user's objective or question using ONLY the provided evidence citations.

USER QUESTION / OBJECTIVE:
{query}

ALLOWED CITATION IDENTIFIERS:
{allowed_list}

EVIDENCE PACKAGE:
{evidence_text}

STRICT INSTRUCTIONS:
1. Respond ONLY with a valid JSON object matching the schema below.
   Do not wrap in extra prose outside JSON.
2. Use ONLY citation identifiers listed under ALLOWED CITATION IDENTIFIERS ({allowed_list}).
3. Do NOT invent document identifiers, locators, page numbers, or unsupported citations.
4. Every paragraph or claim MUST list exact citation_ids (e.g. ["CIT-1"]) supporting it.
5. If evidence is missing or insufficient, state this in "insufficient_evidence".
6. If evidence sources contradict each other, put an explanation in "contradictions".

EXPECTED JSON SCHEMA:
{{
  "schema_version": "1",
  "paragraphs": [
    {{
      "paragraph_id": "PAR-1",
      "text": "Your synthesized paragraph text here.",
      "citation_ids": ["CIT-1"]
    }}
  ],
  "claims": [
    {{
      "claim_id": "CLM-1",
      "statement": "Specific statement supported by sources.",
      "citation_ids": ["CIT-1", "CIT-2"]
    }}
  ],
  "insufficient_evidence": [
    "Information missing from provided evidence"
  ],
  "contradictions": [
    "Contradiction between CIT-1 and CIT-2"
  ]
}}
"""


def _extract_json_payload(raw_text: str) -> str:
    cleaned = raw_text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


GroundedFailure = Literal["schema_violation", "valid_but_unsupported"]
"""The two ways a reply can be unusable, which have different causes and fixes."""


def classify_grounded_response(
    raw_text: str,
    allowed_citations: set[str],
) -> tuple[GroundedModelResponse | None, GroundedFailure | None, list[str]]:
    """Parse and validate, saying *which* way it failed rather than only that it did.

    The two failures are different problems with different causes. A reply that does not
    parse means the schema was not held to. A reply that parses but cites evidence it was
    never given means the schema was held to and the answer is still unsupported -- the
    failure that gets worse as models get smaller, and the one that a single "invalid"
    counter would hide.

    Returns the response when it is usable, otherwise `"schema_violation"` or
    `"valid_but_unsupported"` alongside the reasons.
    """

    json_str = _extract_json_payload(raw_text)
    try:
        data = json.loads(json_str)
        response = GroundedModelResponse.model_validate(data)
    except Exception as error:
        return None, "schema_violation", [f"malformed model JSON or schema mismatch: {error}"]

    errors: list[str] = []
    for paragraph in response.paragraphs:
        for cid in paragraph.citation_ids:
            if cid not in allowed_citations:
                errors.append(f"model cited unknown or un-retrieved source: {cid}")

    for claim in response.claims:
        for cid in claim.citation_ids:
            if cid not in allowed_citations:
                errors.append(f"model cited unknown or un-retrieved source: {cid}")

    if errors:
        return None, "valid_but_unsupported", errors
    return response, None, []


def parse_and_validate_grounded_response(
    raw_text: str,
    allowed_citations: set[str],
) -> tuple[GroundedModelResponse | None, list[str]]:
    """Parse JSON and validate that all cited sources are present in allowed_citations."""

    response, _failure, errors = classify_grounded_response(raw_text, allowed_citations)
    return response, errors


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_grounded_ask(
    query: str,
    *,
    home: Path | None = None,
    max_sources: int = 8,
) -> AskRunResult:
    """Execute a grounded question workflow over local Archiv evidence."""

    if not query.strip():
        raise ValueError("query cannot be empty")

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    model_config = load_model_config(layout.root)
    run_id = uuid4().hex
    evidence_dir = layout.runs / "ask" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)

    if model_config.adapter == "disabled":
        result = AskRunResult(
            run_id=run_id,
            status=RunStatus.BLOCKED_BY_POLICY,
            query=query,
            evidence_dir=str(evidence_dir),
            model=model_config,
            errors=["model use is disabled; Archiv will not select a hidden fallback"],
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result

    if model_config.adapter == "remote-evaluation":
        # Checked here as well as inside the adapter, and before anything is retrieved
        # or written, so an unmarked archive never even builds a prompt out of its own
        # documents. The adapter keeps its own check because it is the thing that opens
        # the socket; this one exists so the refusal is recorded as policy rather than
        # arriving as a generic failure from the call below.
        try:
            check_evaluation_opt_in(layout.root)
        except EvaluationNotEnabledError as error:
            result = AskRunResult(
                run_id=run_id,
                status=RunStatus.BLOCKED_BY_POLICY,
                query=query,
                evidence_dir=str(evidence_dir),
                model=model_config,
                errors=[str(error)],
            )
            _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
            return result

    retrieval = retrieve_evidence(query, home=layout.root, evidence_limit=max_sources)
    _write_json(
        evidence_dir / "retrieval.json",
        retrieval.diagnostics.model_dump(mode="json"),
    )

    citations_map: dict[str, SearchResult] = {}
    retrieved_citations: list[Citation] = []
    errors: list[str] = []

    for index, res in enumerate(retrieval.results, start=1):
        cid = f"CIT-{index}"
        validation = validate_citation(res.citation, home=layout.root)
        if not validation.valid:
            errors.append(
                f"retrieved source {res.citation.source_name} failed citation validation: "
                + "; ".join(validation.errors)
            )
            continue
        excerpt = read_source_excerpt(res.citation, home=layout.root)
        if excerpt != res.text:
            errors.append(
                f"retrieved source {res.citation.source_name} excerpt mismatch with original"
            )
            continue
        citations_map[cid] = res
        retrieved_citations.append(res.citation)

    if errors:
        result = AskRunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            query=query,
            evidence_dir=str(evidence_dir),
            model=model_config,
            retrieved_citations=retrieved_citations,
            retrieval_diagnostics=retrieval.diagnostics,
            errors=errors,
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result

    if not citations_map:
        grounded_resp = GroundedModelResponse(
            insufficient_evidence=[f"No matching evidence found in Archiv for query: {query}"]
        )
        result = AskRunResult(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            query=query,
            evidence_dir=str(evidence_dir),
            model=model_config,
            retrieved_citations=[],
            retrieval_diagnostics=retrieval.diagnostics,
            grounded_response=grounded_resp.model_dump(mode="json"),
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result

    prompt = build_grounding_prompt(query, citations_map)
    _write_json(
        evidence_dir / "request.json",
        {
            "schema_version": "1",
            "run_id": run_id,
            "query": query,
            "prompt": prompt,
            "allowed_citations": list(citations_map.keys()),
            "model": model_config.model_dump(mode="json"),
        },
    )

    raw_response: str | None = None
    try:
        # Built inside the guard, not before it. A failure here -- an adapter that
        # refuses to be constructed, or one whose configuration is rejected -- would
        # otherwise escape with no run result at all, which is worse than an unstamped
        # one: there would be nothing on disk saying what was attempted.
        adapter = build_model_adapter(model_config, layout.root)
        structured = request_grounded_response(adapter, prompt, set(citations_map.keys()))
        _write_json(
            evidence_dir / "structured_output.json",
            structured.record.model_dump(mode="json"),
        )
        raw_response = json.dumps(
            structured.response.model_dump(mode="json") if structured.response else {},
            indent=2,
            sort_keys=True,
        )
    except Exception as error:
        result = AskRunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            query=query,
            evidence_dir=str(evidence_dir),
            model=model_config,
            retrieved_citations=retrieved_citations,
            retrieval_diagnostics=retrieval.diagnostics,
            errors=[f"model request failed: {type(error).__name__}: {error}"],
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result

    (evidence_dir / "model_response.txt").write_text(raw_response, encoding="utf-8")

    parsed_response = structured.response
    parse_errors = list(structured.errors)

    if parse_errors or parsed_response is None:
        result = AskRunResult(
            run_id=run_id,
            status=RunStatus.PARTIALLY_PRODUCED_BUT_INVALID,
            query=query,
            evidence_dir=str(evidence_dir),
            model=model_config,
            retrieved_citations=retrieved_citations,
            retrieval_diagnostics=retrieval.diagnostics,
            raw_model_response=raw_response,
            errors=parse_errors or ["failed to parse model response"],
        )
        _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
        return result

    result = AskRunResult(
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
        query=query,
        evidence_dir=str(evidence_dir),
        model=model_config,
        retrieved_citations=retrieved_citations,
        retrieval_diagnostics=retrieval.diagnostics,
        raw_model_response=raw_response,
        grounded_response=parsed_response.model_dump(mode="json"),
    )
    _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
    return result
