"""Bounded local visual OCR for images and image-only PDF pages."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.hashing import sha256_file
from archiv.ingestion.limits import MAX_CPU_SECONDS, MAX_MEMORY_BYTES, MAX_SUBPROCESSES

OCR_PROCESSOR_VERSION = "1"
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
MAX_PDF_PAGES = 250
OCR_TIMEOUT_SECONDS = 60
RENDER_DPI = 200
PAGE_BATCH_SIZE = 16

type OcrWord = tuple[str, int, int, int, int, float | None]


class VisualOcrError(RuntimeError):
    """A local OCR or rendering process could not complete safely."""


@dataclass(frozen=True)
class VisualOcrRun:
    """One non-destructive visual OCR processor result."""

    status: str
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
    status_value = manifest.get("status")
    status = status_value if isinstance(status_value, str) else "failed"
    summary: dict[str, object] = {
        "status": status,
        "origin": "visual_ocr",
        "engine": manifest.get("engine"),
        "engine_version": manifest.get("engine_version"),
        "languages": manifest.get("languages", []),
        "pages_processed": manifest.get("pages_processed", 0),
        "complete": manifest.get("complete", False),
        "warnings": manifest.get("warnings", []),
    }
    return VisualOcrRun(status, segments, path, digest, summary, error)


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
            "max_subprocesses": MAX_SUBPROCESSES,
            "cpu_seconds": MAX_CPU_SECONDS,
            "memory_bytes": MAX_MEMORY_BYTES,
        },
    }


def _sandbox_mode() -> str:
    mode = os.environ.get("ARCHIV_OCR_SANDBOX", "auto").strip().lower()
    if mode not in {"auto", "required", "off"}:
        raise VisualOcrError("ARCHIV_OCR_SANDBOX must be auto, required, or off")
    return mode


def _run(
    command: list[str],
    *,
    root: Path,
    timeout: int,
    extra_ro_binds: tuple[Path, ...] = (),
) -> tuple[str, str, str]:
    mode = _sandbox_mode()
    if mode == "required" and os.name != "posix":
        raise VisualOcrError("required bubblewrap isolation is unavailable on this platform")

    sandbox = "none"
    wrapped = command
    if mode != "off" and os.name == "posix":
        bubblewrap = shutil.which("bwrap")
        if bubblewrap is not None:
            writable = root.resolve()
            wrapped = [
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
            ]
            # --tmpfs /tmp masks the earlier --ro-bind / / for anything under /tmp,
            # including inputs whose real path (e.g. ARCHIV_HOME under /tmp) lies
            # outside the writable root re-bound below. Re-expose those explicitly.
            for extra in extra_ro_binds:
                resolved = extra.resolve()
                wrapped += ["--ro-bind", str(resolved), str(resolved)]
            wrapped += [
                "--bind",
                str(writable),
                str(writable),
                "--chdir",
                str(writable),
                "--",
                *command,
            ]
            sandbox = "bubblewrap"
        elif mode == "required":
            raise VisualOcrError("bubblewrap is required but not installed")

    environment = os.environ.copy()
    environment.setdefault("OMP_THREAD_LIMIT", "2")

    def apply_resource_limits() -> None:
        # Imported only in the POSIX child; ``resource`` is unavailable on Windows.
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))

    try:
        completed = subprocess.run(
            wrapped,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            env=environment,
            text=True,
            timeout=timeout,
            preexec_fn=apply_resource_limits if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired as error:
        raise VisualOcrError(f"processor timed out after {timeout} seconds") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise VisualOcrError(
            f"processor exited with status {completed.returncode}: "
            f"{(detail[:500] or 'no diagnostic')}"
        )
    return completed.stdout, completed.stderr, sandbox


def _tool_version(
    executable: str,
    arguments: list[str],
    *,
    root: Path,
) -> tuple[str, str]:
    stdout, stderr, sandbox = _run(
        [executable, *arguments],
        root=root,
        timeout=15,
    )
    text = stdout.strip() or stderr.strip()
    return (text.splitlines()[0] if text else "unknown"), sandbox


def _executable_hash(executable: str) -> str | None:
    try:
        return sha256_file(Path(executable).resolve())
    except OSError:
        return None


def _available_languages(executable: str, *, root: Path) -> list[str]:
    stdout, _, _ = _run([executable, "--list-langs"], root=root, timeout=15)
    return sorted(
        {
            line.strip()
            for line in stdout.splitlines()
            if line.strip() and not line.lower().startswith("list of available languages")
        }
    )


def _select_languages(available: list[str]) -> tuple[list[str], list[str]]:
    configured = os.environ.get("ARCHIV_OCR_LANGUAGES", "").strip()
    if configured:
        selected = [
            token.strip() for token in configured.replace(",", "+").split("+") if token.strip()
        ]
        return selected, [language for language in selected if language not in available]

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


def _image_dimensions(path: Path) -> tuple[int, int]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise VisualOcrError(f"image exceeds OCR input limit of {MAX_INPUT_BYTES} bytes")
    with Image.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise VisualOcrError(f"image exceeds OCR pixel limit of {MAX_IMAGE_PIXELS}")
    return width, height


def _integer(row: dict[str, str | None], key: str) -> int:
    value = row.get(key)
    try:
        return int(value) if value is not None else 0
    except ValueError as error:
        raise VisualOcrError(f"invalid Tesseract TSV integer for {key}: {value}") from error


def _confidence(row: dict[str, str | None]) -> float | None:
    try:
        value = float(row.get("conf") or "-1")
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_tsv(
    payload: str,
    *,
    page: int,
    languages: list[str],
) -> list[NormalizedSegment]:
    lines: dict[tuple[int, int, int], list[OcrWord]] = {}
    order: list[tuple[int, int, int]] = []
    for row in csv.DictReader(io.StringIO(payload), delimiter="\t"):
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = (
            _integer(row, "block_num"),
            _integer(row, "par_num"),
            _integer(row, "line_num"),
        )
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(
            (
                text,
                _integer(row, "left"),
                _integer(row, "top"),
                _integer(row, "width"),
                _integer(row, "height"),
                _confidence(row),
            )
        )

    segments: list[NormalizedSegment] = []
    for line_number, key in enumerate(order, 1):
        words = lines[key]
        left = min(word[1] for word in words)
        top = min(word[2] for word in words)
        right = max(word[1] + word[3] for word in words)
        bottom = max(word[2] + word[4] for word in words)
        scores = [word[5] for word in words if word[5] is not None]
        locator: dict[str, object] = {
            "origin": "visual_ocr",
            "page": page,
            "line": line_number,
            "bbox": [left, top, right, bottom],
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
        if scores:
            locator["confidence"] = round(sum(scores) / len(scores), 3)
        segments.append(
            NormalizedSegment(locator=locator, text=" ".join(word[0] for word in words))
        )
    return segments


def _ocr_image(
    image: Path,
    *,
    page: int,
    root: Path,
    executable: str,
    languages: list[str],
    origin: str,
    relative_path: str | None,
    image_sha256: str,
) -> tuple[list[NormalizedSegment], dict[str, object]]:
    width, height = _image_dimensions(image)
    stdout, stderr, sandbox = _run(
        [
            executable,
            str(image),
            "stdout",
            "-l",
            "+".join(languages),
            "--psm",
            "3",
            "tsv",
        ],
        root=root,
        timeout=OCR_TIMEOUT_SECONDS,
        extra_ro_binds=(image.parent,) if origin == "canonical_original" else (),
    )
    raw_relative = Path("ocr") / f"page-{page:04d}.tsv"
    raw_path = root / raw_relative
    raw_path.write_text(stdout, encoding="utf-8")
    segments = _parse_tsv(stdout, page=page, languages=languages)
    warnings = [f"tesseract diagnostic: {stderr.strip()[:500]}"] if stderr.strip() else []
    return segments, {
        "page": page,
        "status": "succeeded",
        "image_origin": origin,
        "image_path": relative_path,
        "image_sha256": image_sha256,
        "width": width,
        "height": height,
        "raw_output_path": raw_relative.as_posix(),
        "raw_output_sha256": sha256_file(raw_path),
        "segment_count": len(segments),
        "sandbox": sandbox,
        "warnings": warnings,
    }


def _split_batch_tsv(stdout: str, batch_size: int) -> dict[int, str]:
    tsv_lines = stdout.splitlines()
    if not tsv_lines:
        return {idx: "" for idx in range(1, batch_size + 1)}
    header = tsv_lines[0]
    by_page: dict[int, list[str]] = {idx: [header] for idx in range(1, batch_size + 1)}
    for line in tsv_lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) > 1:
            try:
                p_num = int(parts[1])
                if p_num in by_page:
                    by_page[p_num].append(line)
            except ValueError:
                continue
    return {idx: "\n".join(lines) + "\n" for idx, lines in by_page.items()}


def _ocr_batch(
    batch: list[tuple[int, Path, str, str | None, str, dict[str, object] | None]],
    *,
    root: Path,
    executable: str,
    languages: list[str],
) -> list[tuple[list[NormalizedSegment], dict[str, object]]]:
    batch_list_path = root / "ocr" / f".batch-{uuid4().hex}.txt"
    batch_list_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        batch_list_path.write_text(
            "\n".join(str(img.resolve()) for _, img, _, _, _, _ in batch) + "\n",
            encoding="utf-8",
        )
        extra_ro_binds = tuple(
            sorted(
                set(
                    img.resolve().parent
                    for _, img, origin, _, _, _ in batch
                    if origin == "canonical_original"
                )
            )
        )
        timeout = max(OCR_TIMEOUT_SECONDS, 10 * len(batch))
        stdout, stderr, sandbox = _run(
            [
                executable,
                str(batch_list_path),
                "stdout",
                "-l",
                "+".join(languages),
                "--psm",
                "3",
                "tsv",
            ],
            root=root,
            timeout=timeout,
            extra_ro_binds=extra_ro_binds,
        )
    finally:
        batch_list_path.unlink(missing_ok=True)

    split_tsv = _split_batch_tsv(stdout, len(batch))
    warnings = [f"tesseract diagnostic: {stderr.strip()[:500]}"] if stderr.strip() else []
    results: list[tuple[list[NormalizedSegment], dict[str, object]]] = []

    for idx, (page, image, origin, relative_path, image_sha256, render) in enumerate(batch, 1):
        width, height = _image_dimensions(image)
        page_tsv = split_tsv.get(idx, "")
        raw_relative = Path("ocr") / f"page-{page:04d}.tsv"
        raw_path = root / raw_relative
        raw_path.write_text(page_tsv, encoding="utf-8")
        segments = _parse_tsv(page_tsv, page=page, languages=languages)
        page_evidence: dict[str, object] = {
            "page": page,
            "status": "succeeded",
            "image_origin": origin,
            "image_path": relative_path,
            "image_sha256": image_sha256,
            "width": width,
            "height": height,
            "raw_output_path": raw_relative.as_posix(),
            "raw_output_sha256": sha256_file(raw_path),
            "segment_count": len(segments),
            "sandbox": sandbox,
            "warnings": list(warnings),
        }
        if render is not None:
            page_evidence["rendered_page"] = render
        results.append((segments, page_evidence))

    return results


MIN_NATIVE_CHARS_PER_PAGE = 25


def _pdf_pages(document: NormalizedDocument) -> tuple[int, list[int]]:
    page_count = document.metadata.get("pages")
    if not isinstance(page_count, int) or page_count < 0:
        raise VisualOcrError("normalized PDF does not report a valid page count")
    text_by_page: dict[int, list[str]] = {}
    for segment in document.segments:
        page = segment.locator.get("page")
        if isinstance(page, int):
            text_by_page.setdefault(page, []).append(segment.text)
    pages_to_ocr = [
        page
        for page in range(1, page_count + 1)
        if len("\n".join(text_by_page.get(page, [])).strip()) < MIN_NATIVE_CHARS_PER_PAGE
    ]
    return page_count, pages_to_ocr


def _render_pdf_page(
    original: Path,
    *,
    page: int,
    root: Path,
    executable: str | None,
) -> tuple[Path, dict[str, object]]:
    relative = Path("previews") / "pages" / f"page-{page:04d}.png"
    image = root / relative
    image.parent.mkdir(parents=True, exist_ok=True)

    if executable is None:
        with contextlib.suppress(Exception):
            import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]

            doc = pdfium.PdfDocument(original)
            if 1 <= page <= len(doc):
                p_obj = doc[page - 1]
                scale = max(1, int(RENDER_DPI / 72.0))
                pil_img = p_obj.render(scale=scale).to_pil()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                pil_img.save(image, format="PNG")  # pyright: ignore[reportUnknownMemberType]
                width, height = _image_dimensions(image)
                return image, {
                    "path": relative.as_posix(),
                    "sha256": sha256_file(image),
                    "width": width,
                    "height": height,
                    "dpi": RENDER_DPI,
                    "sandbox": "none",
                    "renderer": "pypdfium2",
                    "warnings": [],
                }
        raise VisualOcrError("no PDF page renderer (pdftoppm or pypdfium2) available")

    _, stderr, sandbox = _run(
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
            str(image.with_suffix("")),
        ],
        root=root,
        timeout=OCR_TIMEOUT_SECONDS,
        extra_ro_binds=(original.parent,),
    )
    if not image.is_file():
        raise VisualOcrError("pdftoppm did not produce the expected page image")
    width, height = _image_dimensions(image)
    warnings = [f"pdftoppm diagnostic: {stderr.strip()[:500]}"] if stderr.strip() else []
    return image, {
        "path": relative.as_posix(),
        "sha256": sha256_file(image),
        "width": width,
        "height": height,
        "dpi": RENDER_DPI,
        "sandbox": sandbox,
        "warnings": warnings,
    }


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

    tesseract = shutil.which("tesseract")
    if tesseract is None:
        manifest["reason"] = "Tesseract executable not installed"
        return _finish(root, manifest, [])

    try:
        engine_version, engine_sandbox = _tool_version(
            tesseract,
            ["--version"],
            root=root,
        )
        available = _available_languages(tesseract, root=root)
        languages, missing = _select_languages(available)
    except VisualOcrError as error:
        manifest.update({"status": "failed", "reason": str(error)})
        return _finish(root, manifest, [], error=str(error))

    manifest.update(
        {
            "engine": "tesseract",
            "engine_version": engine_version,
            "engine_executable_sha256": _executable_hash(tesseract),
            "engine_sandbox": engine_sandbox,
            "available_languages": available,
            "languages": languages,
        }
    )
    if missing:
        manifest.update(
            {
                "reason": "requested OCR languages are not installed",
                "missing_languages": missing,
            }
        )
        return _finish(root, manifest, [])
    if not languages:
        manifest["reason"] = "Tesseract has no usable installed language model"
        return _finish(root, manifest, [])

    inputs: list[tuple[int, Path, str, str | None, str, dict[str, object] | None]] = []
    pages: list[dict[str, object]] = []
    warnings: list[str] = []
    if normalized.kind == "image":
        frames = normalized.metadata.get("frames", 1)
        if isinstance(frames, int) and frames > 1:
            try:
                pages_dir = root / "previews" / "pages"
                pages_dir.mkdir(parents=True, exist_ok=True)
                with Image.open(original) as img:
                    for frame_idx in range(frames):
                        img.seek(frame_idx)
                        page_num = frame_idx + 1
                        rel_path = Path("previews") / "pages" / f"page-{page_num:04d}.png"
                        out_path = root / rel_path
                        if not out_path.is_file():
                            frame_copy = img.copy()
                            if frame_copy.mode not in ("RGB", "RGBA"):
                                frame_copy = frame_copy.convert("RGB")
                            frame_copy.save(out_path, format="PNG")
                        inputs.append(
                            (
                                page_num,
                                out_path,
                                "rendered_image_frame",
                                rel_path.as_posix(),
                                sha256_file(out_path),
                                None,
                            )
                        )
            except Exception as error:
                manifest.update(
                    {"status": "failed", "reason": f"failed to unpack multi-frame image: {error}"}
                )
                return _finish(root, manifest, [], error=str(error))
        else:
            inputs.append((1, original, "canonical_original", None, normalized.object_sha256, None))
    elif normalized.kind == "svg":
        resvg = shutil.which("resvg")
        if resvg is None:
            manifest["reason"] = "resvg is required for SVG visual OCR"
            return _finish(root, manifest, [])
        try:
            rel_path = Path("previews") / "pages" / "page-0001.png"
            out_path = root / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not out_path.is_file():
                subprocess.run(
                    [resvg, str(original), str(out_path)],
                    check=True,
                    capture_output=True,
                    timeout=OCR_TIMEOUT_SECONDS,
                )
            inputs.append(
                (
                    1,
                    out_path,
                    "rendered_svg_page",
                    rel_path.as_posix(),
                    sha256_file(out_path),
                    None,
                )
            )
        except Exception as error:
            manifest.update({"status": "failed", "reason": f"failed to render SVG: {error}"})
            return _finish(root, manifest, [], error=str(error))
    elif normalized.kind == "pdf":
        try:
            page_count, empty_pages = _pdf_pages(normalized)
        except VisualOcrError as error:
            manifest.update({"status": "failed", "reason": str(error)})
            return _finish(root, manifest, [], error=str(error))
        manifest.update({"pdf_pages": page_count, "pages_requiring_ocr": empty_pages})
        if page_count > MAX_PDF_PAGES:
            manifest["reason"] = f"PDF exceeds OCR page limit of {MAX_PDF_PAGES} pages"
            return _finish(root, manifest, [])
        if not empty_pages:
            manifest["reason"] = "native text is available for every PDF page"
            return _finish(root, manifest, [])
        renderer = shutil.which("pdftoppm")
        pdfium_mod = None
        with contextlib.suppress(ImportError):
            import pypdfium2 as _pdfium  # pyright: ignore[reportMissingTypeStubs]

            pdfium_mod = _pdfium

        if renderer is None and pdfium_mod is None:
            manifest["reason"] = "pdftoppm or pypdfium2 is required for image-only PDF pages"
            return _finish(root, manifest, [])

        if renderer is not None:
            try:
                renderer_version, renderer_sandbox = _tool_version(renderer, ["-v"], root=root)
            except VisualOcrError as error:
                manifest.update({"status": "failed", "reason": str(error)})
                return _finish(root, manifest, [], error=str(error))
            manifest.update(
                {
                    "renderer": "pdftoppm",
                    "renderer_version": renderer_version,
                    "renderer_executable_sha256": _executable_hash(renderer),
                    "renderer_sandbox": renderer_sandbox,
                    "render_dpi": RENDER_DPI,
                }
            )
        else:
            manifest.update(
                {
                    "renderer": "pypdfium2",
                    "renderer_version": getattr(pdfium_mod, "__version__", "5.x"),
                    "render_dpi": RENDER_DPI,
                }
            )
        for page in empty_pages:
            try:
                image, render = _render_pdf_page(
                    original,
                    page=page,
                    root=root,
                    executable=renderer,
                )
                inputs.append(
                    (
                        page,
                        image,
                        "rendered_pdf_page",
                        str(render["path"]),
                        str(render["sha256"]),
                        render,
                    )
                )
            except (OSError, VisualOcrError) as error:
                pages.append(
                    {
                        "page": page,
                        "status": "failed",
                        "stage": "render",
                        "error": str(error),
                    }
                )
                warnings.append(f"page {page} render failed: {error}")
    else:
        manifest["reason"] = f"visual OCR is not applicable to {normalized.kind}"
        return _finish(root, manifest, [])

    segments: list[NormalizedSegment] = []
    completed = 0
    for batch_start in range(0, len(inputs), PAGE_BATCH_SIZE):
        batch = inputs[batch_start : batch_start + PAGE_BATCH_SIZE]
        if len(batch) == 1:
            page, image, origin, relative, image_hash, render = batch[0]
            try:
                page_segments, evidence = _ocr_image(
                    image,
                    page=page,
                    root=root,
                    executable=tesseract,
                    languages=languages,
                    origin=origin,
                    relative_path=relative,
                    image_sha256=image_hash,
                )
                if render is not None:
                    evidence["rendered_page"] = render
                segments.extend(page_segments)
                pages.append(evidence)
                completed += 1
            except (OSError, VisualOcrError) as error:
                pages.append(
                    {
                        "page": page,
                        "status": "failed",
                        "stage": "recognition",
                        "error": str(error),
                    }
                )
                warnings.append(f"page {page} OCR failed: {error}")
        else:
            try:
                batch_results = _ocr_batch(
                    batch,
                    root=root,
                    executable=tesseract,
                    languages=languages,
                )
                for page_segments, evidence in batch_results:
                    segments.extend(page_segments)
                    pages.append(evidence)
                    completed += 1
            except Exception as batch_error:
                warnings.append(f"batch OCR failed ({batch_error}); falling back to single-page")
                for page, image, origin, relative, image_hash, render in batch:
                    try:
                        page_segments, evidence = _ocr_image(
                            image,
                            page=page,
                            root=root,
                            executable=tesseract,
                            languages=languages,
                            origin=origin,
                            relative_path=relative,
                            image_sha256=image_hash,
                        )
                        if render is not None:
                            evidence["rendered_page"] = render
                        segments.extend(page_segments)
                        pages.append(evidence)
                        completed += 1
                    except (OSError, VisualOcrError) as error:
                        pages.append(
                            {
                                "page": page,
                                "status": "failed",
                                "stage": "recognition",
                                "error": str(error),
                            }
                        )
                        warnings.append(f"page {page} OCR failed: {error}")

    failed = sum(1 for page in pages if page.get("status") == "failed")
    manifest.update(
        {
            "status": "succeeded" if completed else "failed",
            "complete": failed == 0,
            "pages_processed": completed,
            "segments": len(segments),
            "pages": pages,
            "warnings": warnings,
        }
    )
    if not completed:
        error = "visual OCR did not complete for any page"
        manifest["reason"] = error
        return _finish(root, manifest, [], error=error)
    return _finish(root, manifest, segments)


__all__ = ["VisualOcrRun", "run_visual_ocr"]
