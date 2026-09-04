"""Rebuildable derived-data construction and provenance."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from archiv.contracts import NormalizedDocument, ProcessingEvidence
from archiv.hashing import sha256_file
from archiv.ingestion.ledger import now_iso, record_processing, record_processing_batch
from archiv.ingestion.normalizers import normalize
from archiv.ingestion.visual_ocr import VisualOcrRun, run_visual_ocr
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout
from archiv.storage.queue import enqueue_job

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
    pending_records: list[tuple[ProcessingEvidence, str, str]],
    item: ProcessingEvidence,
    *,
    started_at: str,
) -> None:
    evidence.append(item)
    pending_records.append((item, started_at, now_iso()))


def derive(
    original: Path,
    digest: str,
    source_name: str,
    layout: ArchivLayout,
    database: ArchivDatabase,
    *,
    replace: bool,
    normalized: NormalizedDocument | None = None,
) -> list[ProcessingEvidence]:
    """Build normalized, extracted, table, and processor-status artifacts."""

    root = layout.derived_root(digest)
    if replace and root.exists():
        shutil.rmtree(root)
    for category in DERIVED_CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)

    started_at = now_iso()
    if normalized is None:
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
        enqueue_job(
            database,
            digest,
            "archiv.visual-ocr",
            processor_version="1",
            state="completed" if ocr_run.status == "succeeded" else "failed",
        )

    evidence: list[ProcessingEvidence] = []
    pending_records: list[tuple[ProcessingEvidence, str, str]] = []

    normalized_path = root / "normalized" / "document.json"
    normalized_item = ProcessingEvidence(
        processor="archiv.normalizer",
        processor_version="1",
        status="succeeded",
        output_kind="normalized-document",
        output_path=str(normalized_path),
        output_sha256=_write_json(normalized_path, normalized.model_dump(mode="json")),
    )
    _append(evidence, pending_records, normalized_item, started_at=started_at)

    text = "\n".join(segment.text for segment in normalized.segments if segment.text)
    if text:
        text_path = root / "extracted" / "text.txt"
        text_path.write_text(text + "\n", encoding="utf-8")
        _append(
            evidence,
            pending_records,
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
            pending_records,
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
            pending_records,
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
        _derive_image_preview(
            evidence, pending_records, digest, root, original, normalized.metadata, started_at
        )
    if normalized.kind == "svg":
        _derive_svg_preview(
            evidence, pending_records, digest, root, original, normalized.metadata, started_at
        )
    if normalized.kind == "audio":
        _derive_audio_status(evidence, pending_records, digest, root, started_at)
    if normalized.kind == "archive":
        _derive_archive_status(
            evidence, pending_records, database, digest, root, normalized.metadata, started_at
        )

    record_processing_batch(database, digest, pending_records)
    return evidence


def _derive_image_preview(
    evidence: list[ProcessingEvidence],
    pending_records: list[tuple[ProcessingEvidence, str, str]],
    digest: str,
    root: Path,
    original: Path,
    metadata: dict[str, object],
    started_at: str,
) -> None:
    preview_path = root / "previews" / "metadata.json"
    _append(
        evidence,
        pending_records,
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

    frames = metadata.get("frames", 1)
    if isinstance(frames, int) and frames > 1:
        try:
            with Image.open(original) as img:
                pages_dir = root / "previews" / "pages"
                pages_dir.mkdir(parents=True, exist_ok=True)
                for frame_idx in range(frames):
                    img.seek(frame_idx)
                    page_path = pages_dir / f"page-{frame_idx + 1:04d}.png"
                    if not page_path.exists():
                        frame_img = img.copy()
                        if frame_img.mode not in ("RGB", "RGBA"):
                            frame_img = frame_img.convert("RGB")
                        frame_img.save(page_path, format="PNG")
        except Exception:
            pass

    thumbnail_path = root / "previews" / "thumbnail.webp"
    try:
        with Image.open(original) as img:
            thumb = img.copy()
            thumb.thumbnail((256, 256))
            if thumb.mode not in ("RGB", "RGBA"):
                thumb = thumb.convert("RGB")
            thumb.save(thumbnail_path, format="WEBP", quality=80)
        _append(
            evidence,
            pending_records,
            ProcessingEvidence(
                processor="archiv.thumbnail",
                processor_version="1",
                status="succeeded",
                output_kind="thumbnail",
                output_path=str(thumbnail_path),
                output_sha256=sha256_file(thumbnail_path),
            ),
            started_at=started_at,
        )
    except Exception as error:
        _append(
            evidence,
            pending_records,
            ProcessingEvidence(
                processor="archiv.thumbnail",
                processor_version="1",
                status="skipped",
                output_kind="thumbnail",
                output_path=str(thumbnail_path),
                error=f"{type(error).__name__}: {error}",
            ),
            started_at=started_at,
        )


def _derive_svg_preview(
    evidence: list[ProcessingEvidence],
    pending_records: list[tuple[ProcessingEvidence, str, str]],
    digest: str,
    root: Path,
    original: Path,
    metadata: dict[str, object],
    started_at: str,
) -> None:
    preview_path = root / "previews" / "metadata.json"
    _append(
        evidence,
        pending_records,
        ProcessingEvidence(
            processor="archiv.svg-metadata",
            processor_version="1",
            status="succeeded",
            output_kind="preview-metadata",
            output_path=str(preview_path),
            output_sha256=_write_json(preview_path, metadata),
        ),
        started_at=started_at,
    )

    resvg = shutil.which("resvg")
    thumbnail_path = root / "previews" / "thumbnail.webp"
    if resvg is None:
        _append(
            evidence,
            pending_records,
            ProcessingEvidence(
                processor="archiv.svg-preview",
                processor_version="1",
                status="skipped",
                output_kind="thumbnail",
                output_path=str(thumbnail_path),
                error="resvg executable not installed",
            ),
            started_at=started_at,
        )
        return

    try:
        tmp_png = root / "previews" / "rendered.png"
        subprocess.run(
            [resvg, "-w", "256", "-h", "256", str(original), str(tmp_png)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        with Image.open(tmp_png) as img:
            thumb = img.copy()
            if thumb.mode not in ("RGB", "RGBA"):
                thumb = thumb.convert("RGB")
            thumb.save(thumbnail_path, format="WEBP", quality=80)
        tmp_png.unlink(missing_ok=True)
        _append(
            evidence,
            pending_records,
            ProcessingEvidence(
                processor="archiv.svg-preview",
                processor_version="1",
                status="succeeded",
                output_kind="thumbnail",
                output_path=str(thumbnail_path),
                output_sha256=sha256_file(thumbnail_path),
            ),
            started_at=started_at,
        )
    except Exception as error:
        _append(
            evidence,
            pending_records,
            ProcessingEvidence(
                processor="archiv.svg-preview",
                processor_version="1",
                status="failed",
                output_kind="thumbnail",
                output_path=str(thumbnail_path),
                error=f"{type(error).__name__}: {error}",
            ),
            started_at=started_at,
        )


def _derive_audio_status(
    evidence: list[ProcessingEvidence],
    pending_records: list[tuple[ProcessingEvidence, str, str]],
    digest: str,
    root: Path,
    started_at: str,
) -> None:
    transcript_path = root / "transcripts" / "status.json"
    _append(
        evidence,
        pending_records,
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


def _derive_archive_status(
    evidence: list[ProcessingEvidence],
    pending_records: list[tuple[ProcessingEvidence, str, str]],
    database: ArchivDatabase,
    digest: str,
    root: Path,
    metadata: dict[str, object],
    started_at: str,
) -> None:
    is_locked = bool(metadata.get("archive_locked"))
    reason = str(metadata.get("reason") or "unsupported")
    status = "skipped" if is_locked else "succeeded"
    error = f"archive locked: {reason}" if is_locked else None
    _append(
        evidence,
        pending_records,
        ProcessingEvidence(
            processor="archiv.archive-extract",
            processor_version="1",
            status=status,
            output_kind="archive-members",
            error=error,
        ),
        started_at=started_at,
    )
    enqueue_job(
        database,
        digest,
        "archiv.archive-extract",
        processor_version="1",
        state="completed" if not is_locked else "failed",
    )
