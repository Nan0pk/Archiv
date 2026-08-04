"""Rebuildable derived-data construction and provenance."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from archiv.contracts import ProcessingEvidence
from archiv.hashing import sha256_file
from archiv.ingestion.ledger import now_iso, record_processing
from archiv.ingestion.normalizers import normalize
from archiv.ingestion.visual_ocr import VisualOcrRun, run_visual_ocr
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout

DERIVED_CATEGORIES = (
    "normalized",
    "extracted",
    "ocr",
    "transcripts",
    "tables",
    "previews",
)


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return sha256_file(path)


def reuse_derived(
    digest: str,
    layout: ArchivLayout,
    database: ArchivDatabase,
) -> list[ProcessingEvidence]:
    """Record reuse of an already-built normalized representation."""

    normalized_path = layout.derived_root(digest) / "normalized" / "document.json"
    if not normalized_path.is_file():
        raise FileNotFoundError("existing object has no normalized derived document")
    item = ProcessingEvidence(
        processor="archiv.derive",
        processor_version="1",
        status="skipped",
        output_kind="derived-existing",
        output_path=str(normalized_path),
        output_sha256=sha256_file(normalized_path),
    )
    timestamp = now_iso()
    record_processing(
        database,
        digest,
        item,
        started_at=timestamp,
        finished_at=timestamp,
    )
    return [item]


def _append(
    evidence: list[ProcessingEvidence],
    database: ArchivDatabase,
    digest: str,
    item: ProcessingEvidence,
    *,
    started_at: str,
) -> None:
    evidence.append(item)
    record_processing(
        database,
        digest,
        item,
        started_at=started_at,
        finished_at=now_iso(),
    )


def derive(
    original: Path,
    digest: str,
    source_name: str,
    layout: ArchivLayout,
    database: ArchivDatabase,
    *,
    replace: bool,
) -> list[ProcessingEvidence]:
    """Build normalized, extracted, table, and processor-status artifacts."""

    root = layout.derived_root(digest)
    if replace and root.exists():
        shutil.rmtree(root)
    for category in DERIVED_CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)

    started_at = now_iso()
    try:
        normalized = normalize(original, digest, source_name=source_name)
    except Exception as error:
        item = ProcessingEvidence(
            processor="archiv.normalizer",
            processor_version="1",
            status="failed",
            output_kind="normalized-document",
            error=f"{type(error).__name__}: {error}",
        )
        record_processing(
            database,
            digest,
            item,
            started_at=started_at,
            finished_at=now_iso(),
        )
        raise

    ocr_run: VisualOcrRun | None = None
    if normalized.kind in {"image", "pdf"}:
        ocr_run = run_visual_ocr(original, normalized, root)
        normalized.segments.extend(ocr_run.segments)
        normalized.metadata["visual_ocr"] = ocr_run.summary

    evidence: list[ProcessingEvidence] = []
    normalized_path = root / "normalized" / "document.json"
    normalized_item = ProcessingEvidence(
        processor="archiv.normalizer",
        processor_version="1",
        status="succeeded",
        output_kind="normalized-document",
        output_path=str(normalized_path),
        output_sha256=_write_json(normalized_path, normalized.model_dump(mode="json")),
    )
    _append(evidence, database, digest, normalized_item, started_at=started_at)

    text = "\n".join(segment.text for segment in normalized.segments if segment.text)
    if text:
        text_path = root / "extracted" / "text.txt"
        text_path.write_text(text + "\n", encoding="utf-8")
        _append(
            evidence,
            database,
            digest,
            ProcessingEvidence(
                processor="archiv.text-export",
                processor_version="1",
                status="succeeded",
                output_kind="extracted-text",
                output_path=str(text_path),
                output_sha256=sha256_file(text_path),
            ),
            started_at=started_at,
        )

    if normalized.tables:
        table_path = root / "tables" / "tables.json"
        _append(
            evidence,
            database,
            digest,
            ProcessingEvidence(
                processor="archiv.table-export",
                processor_version="1",
                status="succeeded",
                output_kind="tables",
                output_path=str(table_path),
                output_sha256=_write_json(
                    table_path,
                    [table.model_dump(mode="json") for table in normalized.tables],
                ),
            ),
            started_at=started_at,
        )

    if ocr_run is not None:
        _append(
            evidence,
            database,
            digest,
            ProcessingEvidence(
                processor="archiv.visual-ocr",
                processor_version="1",
                status=ocr_run.status,
                output_kind="visual-ocr-manifest",
                output_path=str(ocr_run.manifest_path),
                output_sha256=ocr_run.manifest_sha256,
                error=ocr_run.error,
            ),
            started_at=started_at,
        )

    if normalized.kind == "image":
        _derive_image_preview(evidence, database, digest, root, normalized.metadata, started_at)
    if normalized.kind == "audio":
        _derive_audio_status(evidence, database, digest, root, started_at)
    return evidence


def _derive_image_preview(
    evidence: list[ProcessingEvidence],
    database: ArchivDatabase,
    digest: str,
    root: Path,
    metadata: dict[str, object],
    started_at: str,
) -> None:
    preview_path = root / "previews" / "metadata.json"
    _append(
        evidence,
        database,
        digest,
        ProcessingEvidence(
            processor="archiv.image-metadata",
            processor_version="1",
            status="succeeded",
            output_kind="preview-metadata",
            output_path=str(preview_path),
            output_sha256=_write_json(preview_path, metadata),
        ),
        started_at=started_at,
    )


def _derive_audio_status(
    evidence: list[ProcessingEvidence],
    database: ArchivDatabase,
    digest: str,
    root: Path,
    started_at: str,
) -> None:
    transcript_path = root / "transcripts" / "status.json"
    _append(
        evidence,
        database,
        digest,
        ProcessingEvidence(
            processor="archiv.transcription",
            processor_version="1",
            status="skipped",
            output_kind="transcript-status",
            output_path=str(transcript_path),
            output_sha256=_write_json(
                transcript_path,
                {
                    "status": "not_run",
                    "reason": "transcription processor not installed",
                },
            ),
        ),
        started_at=started_at,
    )
