"""Bounded local visual OCR for images and image-only PDF pages."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.hashing import sha256_file

OCR_PROCESSOR_VERSION = "1"
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
MAX_PDF_PAGES = 250
OCR_TIMEOUT_SECONDS = 60
RENDER_DPI = 200

OcrStatus = Literal["succeeded", "skipped", "failed"]
SandboxKind = Literal["bubblewrap", "none"]


class VisualOcrError(RuntimeError):
    """A configured OCR or rendering process could not complete safely."""


class VisualOcrConfigurationError(VisualOcrError):
    """OCR configuration is invalid or cannot be satisfied."""


class VisualOcrPolicyError(VisualOcrError):
    """An input exceeds the bounded visual-processing policy."""


@dataclass(frozen=True)
class CommandOutput:
    """Captured bounded subprocess output and its isolation mode."""

    stdout: str
    stderr: str
    sandbox: SandboxKind
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageRender:
    """One deterministic page raster generated from a PDF."""

    path: Path
    relative_path: str
    sha256: str
    width: int
    height: int
    sandbox: SandboxKind
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PageOcr:
    """OCR-derived segments and evidence for one image or rendered page."""

    segments: list[NormalizedSegment]
    evidence: dict[str, object]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class VisualOcrRun:
    """One non-destructive visual OCR processor result."""

    status: OcrStatus
    segments: list[NormalizedSegment]
    manifest_path: Path
    manifest_sha256: str
    summary: dict[str, object]
    error: str | None = None


def _write_manifest(root: Path, manifest: dict[str, object]) -> tuple[Path, str]:
    path = root / "ocr" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path, sha256_file(path)


def _finish(
    root: Path,
    manifest: dict[str, object],
    segments: list[NormalizedSegment],
    *,
    error: str | None = None,
) -> VisualOcrRun:
    path, digest = _write_manifest(root, manifest)
    summary: dict[str, object] = {
        "status": manifest["status"],
        "origin": "visual_ocr",
        "engine": manifest.get("engine"),
        "engine_version": manifest.get("engine_version"),
        "languages": manifest.get("languages", []),
        "pages_processed": manifest.get("pages_processed", 0),
        "complete": manifest.get("complete", False),
        "warnings": manifest.get("warnings", []),
    }
    return VisualOcrRun(
        status=str(manifest["status"]),  # type: ignore[arg-type]
        segments=segments,
        manifest_path=path,
        manifest_sha256=digest,
        summary=summary,
        error=error,
    )


def _base_manifest(source_sha256: str, source_kind: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "processor": "archiv.visual-ocr",
        "processor_version": OCR_PROCESSOR_VERSION,
        "source_sha256": source_sha256,
        "source_kind": source_kind,
        "status": "skipped",
        "complete": False,
        "pages_processed": 0,
        "pages": [],
        "warnings": [],
        "limits": {
            "max_input_bytes": MAX_INPUT_BYTES,
            "max_image_pixels": MAX_IMAGE_PIXELS,
            "max_pdf_pages": MAX_PDF_PAGES,
            "page_timeout_seconds": OCR_TIMEOUT_SECONDS,
            "render_dpi": RENDER_DPI,
        },
    }


def _sandbox_mode() -> str:
    mode = os.environ.get("ARCHIV_OCR_SANDBOX", "auto").strip().lower()
    if mode not in {"auto", "required", "off"}:
        raise VisualOcrConfigurationError(
            "ARCHIV_OCR_SANDBOX must be one of: auto, required, off"
        )
    return mode


def _wrap_command(
    command: list[str],
    *,
    writable_root: Path,
) -> tuple[list[str], SandboxKind]:
    mode = _sandbox_mode()
    if mode == "off" or os.name != "posix":
        if mode == "required" and os.name != "posix":
            raise VisualOcrConfigurationError(
                "required bubblewrap isolation is unavailable on this platform"
            )
        return command, "none"

    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        if mode == "required":
            raise VisualOcrConfigurationError("bubblewrap is required but not installed")
        return command, "none"

    writable = writable_root.resolve()
    return (
        [
            bubblewrap,
            "--unshare-net",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(writable),
            str(writable),
            "--chdir",
            str(writable),
            "--",
            *command,
        ],
        "bubblewrap",
    )


def _execute(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.setdefault("OMP_THREAD_LIMIT", "2")
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=environment,
        text=True,
        timeout=timeout,
    )


def _run_command(
    command: list[str],
    *,
    writable_root: Path,
    timeout: int,
) -> CommandOutput:
    wrapped, sandbox = _wrap_command(command, writable_root=writable_root)
    try:
        completed = _execute(wrapped, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise VisualOcrError(f"processor timed out after {timeout} seconds") from error

    warnings: list[str] = []
    if completed.returncode != 0 and sandbox == "bubblewrap" and _sandbox_mode() == "auto":
        try:
            completed = _execute(command, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise VisualOcrError(f"processor timed out after {timeout} seconds") from error
        sandbox = "none"
        warnings.append(
            "bubblewrap isolation was unavailable; the local processor ran without a network namespace"
        )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        raise VisualOcrError(
            f"processor exited with status {completed.returncode}: {detail or 'no diagnostic'}"
        )
    return CommandOutput(
        stdout=completed.stdout,
        stderr=completed.stderr,
        sandbox=sandbox,
        warnings=tuple(warnings),
    )


def _first_output_line(output: CommandOutput) -> str:
    combined = output.stdout.strip() or output.stderr.strip()
    return combined.splitlines()[0] if combined else "unknown"


def _executable_evidence(path: str) -> dict[str, object]:
    resolved = Path(path).resolve()
    try:
        digest: str | None = sha256_file(resolved)
    except OSError:
        digest = None
    return {"name": resolved.name, "sha256": digest}


def _available_languages(
    executable: str,
    *,
    root: Path,
) -> tuple[list[str], CommandOutput]:
    output = _run_command(
        [executable, "--list-langs"],
        writable_root=root,
        timeout=15,
    )
    languages = [
        line.strip()
        for line in output.stdout.splitlines()
        if line.strip() and not line.lower().startswith("list of available languages")
    ]
    return sorted(set(languages)), output


def _requested_languages(available: list[str]) -> tuple[list[str], list[str]]:
    configured = os.environ.get("ARCHIV_OCR_LANGUAGES", "").strip()
    if configured:
        requested = [
            token.strip()
            for token in configured.replace(",", "+").split("+")
            if token.strip()
        ]
        missing = [language for language in requested if language not in available]
        return requested, missing

    selected: list[str] = []
    if "eng" in available:
        selected.append("eng")
    if "urd_naw" in available:
        selected.append("urd_naw")
    elif "urd" in available:
        selected.append("urd")
    if "ara" in available:
        selected.append("ara")
    if not selected:
        selected = [language for language in available if language != "osd"][:1]
    return selected, []


def _inspect_image(path: Path) -> tuple[int, int]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise VisualOcrPolicyError(
            f"image exceeds OCR input limit of {MAX_INPUT_BYTES} bytes"
        )
    with Image.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise VisualOcrPolicyError(
            f"image exceeds OCR pixel limit of {MAX_IMAGE_PIXELS} pixels"
        )
    return width, height


def _parse_int(row: dict[str, str | None], key: str) -> int:
    value = row.get(key)
    if value is None:
        raise VisualOcrError(f"Tesseract TSV is missing {key}")
    try:
        return int(value)
    except ValueError as error:
        raise VisualOcrError(f"invalid Tesseract TSV integer for {key}: {value}") from error


def _parse_confidence(row: dict[str, str | None]) -> float | None:
    value = row.get("conf")
    if value is None:
        return None
    try:
        confidence = float(value)
    except ValueError:
        return None
    return confidence if confidence >= 0 else None


def _parse_tesseract_tsv(
    payload: str,
    *,
    page: int,
    languages: list[str],
) -> list[NormalizedSegment]:
    reader = csv.DictReader(io.StringIO(payload), delimiter="\t")
    lines: dict[tuple[int, int, int], list[tuple[str, int, int, int, int, float | None]]] = {}
    order: list[tuple[int, int, int]] = []
    for row in reader:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = (
            _parse_int(row, "block_num"),
            _parse_int(row, "par_num"),
            _parse_int(row, "line_num"),
        )
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(
            (
                text,
                _parse_int(row, "left"),
                _parse_int(row, "top"),
                _parse_int(row, "width"),
                _parse_int(row, "height"),
                _parse_confidence(row),
            )
        )

    segments: list[NormalizedSegment] = []
    for line_index, key in enumerate(order, 1):
        words = lines[key]
        left = min(word[1] for word in words)
        top = min(word[2] for word in words)
        right = max(word[1] + word[3] for word in words)
        bottom = max(word[2] + word[4] for word in words)
        confidences = [word[5] for word in words if word[5] is not None]
        confidence = (
            round(sum(confidences) / len(confidences), 3) if confidences else None
        )
        locator: dict[str, object] = {
            "origin": "visual_ocr",
            "page": page,
            "line": line_index,
            "region": {
                "x": left,
                "y": top,
                "width": right - left,
                "height": bottom - top,
                "unit": "pixel",
            },
            "engine": "tesseract",
            "languages": list(languages),
        }
        if confidence is not None:
            locator["confidence"] = confidence
        segments.append(
            NormalizedSegment(
                locator=locator,
                text=" ".join(word[0] for word in words),
            )
        )
    return segments


def _ocr_page(
    image: Path,
    *,
    page: int,
    root: Path,
    executable: str,
    languages: list[str],
    image_origin: str,
    image_relative_path: str | None,
    image_sha256: str,
    width: int,
    height: int,
) -> PageOcr:
    language_spec = "+".join(languages)
    output = _run_command(
        [
            executable,
            str(image),
            "stdout",
            "-l",
            language_spec,
            "--psm",
            "3",
            "tsv",
        ],
        writable_root=root,
        timeout=OCR_TIMEOUT_SECONDS,
    )
    raw_relative = Path("ocr") / f"page-{page:04d}.tsv"
    raw_path = root / raw_relative
    raw_path.write_text(output.stdout, encoding="utf-8")
    segments = _parse_tesseract_tsv(
        output.stdout,
        page=page,
        languages=languages,
    )
    warnings = list(output.warnings)
    stderr = output.stderr.strip()
    if stderr:
        warnings.append(f"tesseract diagnostic: {stderr[:500]}")
    evidence: dict[str, object] = {
        "page": page,
        "status": "succeeded",
        "image_origin": image_origin,
        "image_path": image_relative_path,
        "image_sha256": image_sha256,
        "width": width,
        "height": height,
        "raw_output_path": raw_relative.as_posix(),
        "raw_output_sha256": sha256_file(raw_path),
        "segment_count": len(segments),
        "sandbox": output.sandbox,
        "warnings": warnings,
    }
    return PageOcr(
        segments=segments,
        evidence=evidence,
        warnings=tuple(warnings),
    )


def _render_pdf_page(
    original: Path,
    *,
    page: int,
    root: Path,
    executable: str,
) -> PageRender:
    pages_root = root / "previews" / "pages"
    pages_root.mkdir(parents=True, exist_ok=True)
    relative = Path("previews") / "pages" / f"page-{page:04d}.png"
    path = root / relative
    prefix = path.with_suffix("")
    output = _run_command(
        [
            executable,
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-png",
            "-r",
            str(RENDER_DPI),
            str(original),
            str(prefix),
        ],
        writable_root=root,
        timeout=OCR_TIMEOUT_SECONDS,
    )
    if not path.is_file():
        raise VisualOcrError("PDF renderer completed without producing the expected page image")
    width, height = _inspect_image(path)
    warnings = list(output.warnings)
    stderr = output.stderr.strip()
    if stderr:
        warnings.append(f"pdftoppm diagnostic: {stderr[:500]}")
    return PageRender(
        path=path,
        relative_path=relative.as_posix(),
        sha256=sha256_file(path),
        width=width,
        height=height,
        sandbox=output.sandbox,
        warnings=tuple(warnings),
    )


def _pdf_pages_requiring_ocr(document: NormalizedDocument) -> tuple[int, list[int]]:
    pages_value = document.metadata.get("pages")
    if not isinstance(pages_value, int) or pages_value < 0:
        raise VisualOcrError("normalized PDF does not report a valid page count")
    page_text: dict[int, list[str]] = {}
    for segment in document.segments:
        page_value = segment.locator.get("page")
        if isinstance(page_value, int):
            page_text.setdefault(page_value, []).append(segment.text)
    required = [
        page
        for page in range(1, pages_value + 1)
        if not "\n".join(page_text.get(page, [])).strip()
    ]
    return pages_value, required


def run_visual_ocr(
    original: Path,
    normalized: NormalizedDocument,
    root: Path,
) -> VisualOcrRun:
    """Run optional local OCR without mutating the immutable source."""

    manifest = _base_manifest(normalized.object_sha256, normalized.kind)
    if os.environ.get("ARCHIV_OCR", "auto").strip().lower() in {"0", "off", "false"}:
        manifest["reason"] = "OCR disabled by ARCHIV_OCR"
        return _finish(root, manifest, [])

    if original.stat().st_size > MAX_INPUT_BYTES:
        manifest["reason"] = f"source exceeds OCR input limit of {MAX_INPUT_BYTES} bytes"
        return _finish(root, manifest, [])

    executable = shutil.which("tesseract")
    if executable is None:
        manifest["reason"] = "Tesseract executable not installed"
        return _finish(root, manifest, [])

    try:
        version_output = _run_command(
            [executable, "--version"],
            writable_root=root,
            timeout=15,
        )
        available, language_output = _available_languages(executable, root=root)
        languages, missing = _requested_languages(available)
        if missing:
            manifest.update(
                {
                    "reason": "requested OCR languages are not installed",
                    "requested_languages": languages,
                    "missing_languages": missing,
                    "available_languages": available,
                }
            )
            return _finish(root, manifest, [])
        if not languages:
            manifest.update(
                {
                    "reason": "Tesseract has no usable installed language model",
                    "available_languages": available,
                }
            )
            return _finish(root, manifest, [])
    except VisualOcrError as error:
        manifest.update({"status": "failed", "reason": str(error)})
        return _finish(root, manifest, [], error=str(error))

    warnings = [*version_output.warnings, *language_output.warnings]
    manifest.update(
        {
            "engine": "tesseract",
            "engine_version": _first_output_line(version_output),
            "engine_executable": _executable_evidence(executable),
            "languages": languages,
            "available_languages": available,
        }
    )

    page_inputs: list[tuple[int, Path, str, str | None, str, int, int]] = []
    if normalized.kind == "image":
        try:
            width, height = _inspect_image(original)
        except VisualOcrPolicyError as error:
            manifest["reason"] = str(error)
            return _finish(root, manifest, [])
        page_inputs.append(
            (
                1,
                original,
                "canonical_original",
                None,
                normalized.object_sha256,
                width,
                height,
            )
        )
    elif normalized.kind == "pdf":
        try:
            page_count, required_pages = _pdf_pages_requiring_ocr(normalized)
        except VisualOcrError as error:
            manifest.update({"status": "failed", "reason": str(error)})
            return _finish(root, manifest, [], error=str(error))
        manifest["pdf_pages"] = page_count
        manifest["pages_requiring_ocr"] = required_pages
        if page_count > MAX_PDF_PAGES:
            manifest["reason"] = f"PDF exceeds OCR page limit of {MAX_PDF_PAGES} pages"
            return _finish(root, manifest, [])
        if not required_pages:
            manifest["reason"] = "native text is available for every PDF page"
            return _finish(root, manifest, [])
        renderer = shutil.which("pdftoppm")
        if renderer is None:
            manifest["reason"] = "pdftoppm is required to rasterize image-only PDF pages"
            return _finish(root, manifest, [])
        try:
            renderer_version = _run_command(
                [renderer, "-v"],
                writable_root=root,
                timeout=15,
            )
        except VisualOcrError as error:
            manifest.update({"status": "failed", "reason": str(error)})
            return _finish(root, manifest, [], error=str(error))
        manifest.update(
            {
                "renderer": "pdftoppm",
                "renderer_version": _first_output_line(renderer_version),
                "renderer_executable": _executable_evidence(renderer),
                "render_dpi": RENDER_DPI,
            }
        )
        warnings.extend(renderer_version.warnings)
        for page in required_pages:
            try:
                render = _render_pdf_page(
                    original,
                    page=page,
                    root=root,
                    executable=renderer,
                )
                page_inputs.append(
                    (
                        page,
                        render.path,
                        "rendered_pdf_page",
                        render.relative_path,
                        render.sha256,
                        render.width,
                        render.height,
                    )
                )
                warnings.extend(render.warnings)
            except (OSError, VisualOcrError) as error:
                page_entry: dict[str, object] = {
                    "page": page,
                    "status": "failed",
                    "stage": "render",
                    "error": str(error),
                }
                pages = manifest["pages"]
                if isinstance(pages, list):
                    pages.append(page_entry)
                warnings.append(f"page {page} render failed: {error}")
    else:
        manifest["reason"] = f"visual OCR is not applicable to kind {normalized.kind}"
        return _finish(root, manifest, [])

    segments: list[NormalizedSegment] = []
    successful_pages = 0
    for page, image, origin, relative, image_digest, width, height in page_inputs:
        try:
            result = _ocr_page(
                image,
                page=page,
                root=root,
                executable=executable,
                languages=languages,
                image_origin=origin,
                image_relative_path=relative,
                image_sha256=image_digest,
                width=width,
                height=height,
            )
            segments.extend(result.segments)
            successful_pages += 1
            pages = manifest["pages"]
            if isinstance(pages, list):
                pages.append(result.evidence)
            warnings.extend(result.warnings)
        except (OSError, VisualOcrError) as error:
            pages = manifest["pages"]
            if isinstance(pages, list):
                pages.append(
                    {
                        "page": page,
                        "status": "failed",
                        "stage": "recognition",
                        "error": str(error),
                    }
                )
            warnings.append(f"page {page} OCR failed: {error}")

    failed_pages = sum(
        1
        for page in manifest["pages"]
        if isinstance(page, dict) and page.get("status") == "failed"
    )
    manifest.update(
        {
            "status": "succeeded" if successful_pages else "failed",
            "complete": failed_pages == 0,
            "pages_processed": successful_pages,
            "segments": len(segments),
            "warnings": warnings,
        }
    )
    if successful_pages == 0:
        error = "visual OCR did not complete for any page"
        manifest["reason"] = error
        return _finish(root, manifest, [], error=error)
    return _finish(root, manifest, segments)


__all__ = ["VisualOcrRun", "run_visual_ocr"]
