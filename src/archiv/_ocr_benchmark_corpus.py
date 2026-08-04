"""Lawful public fixture rendering and private corpus validation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from archiv._ocr_benchmark_core import (
    FONT_ENV,
    FONT_NAMES,
    MAX_FIXTURE_BYTES,
    MAX_PRIVATE_FIXTURES,
    PUBLIC_FIXTURES,
    SCHEMA_VERSION,
    FixtureRecord,
    FixtureTemplate,
    OcrBenchmarkError,
)
from archiv.hashing import sha256_file


def _find_fonts(role: str) -> list[Path]:
    configured = os.environ.get(FONT_ENV[role], "").strip()
    found = [Path(value).expanduser().resolve() for value in configured.split(os.pathsep) if value]
    missing = [path for path in found if not path.is_file()]
    if missing:
        raise OcrBenchmarkError(f"configured benchmark font does not exist: {missing[0]}")
    for name in FONT_NAMES[role]:
        for root in (Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"):
            if root.is_dir():
                for match in root.rglob(name):
                    path = match.resolve()
                    if path not in found:
                        found.append(path)
    if not found:
        raise OcrBenchmarkError(f"set {FONT_ENV[role]} to a lawful local font")
    return found


def _font_for(
    fonts: Mapping[str, Sequence[Path]],
    fixture: FixtureTemplate,
) -> Path:
    choices = fonts[fixture.font_role]
    if fixture.font_variant >= len(choices):
        raise OcrBenchmarkError(
            f"fixture {fixture.fixture_id} requires another {fixture.font_role} font; "
            f"set {FONT_ENV[fixture.font_role]} to {os.pathsep}-separated paths"
        )
    return choices[fixture.font_variant]


def _draw_line(
    draw: ImageDraw.ImageDraw,
    fixture: FixtureTemplate,
    font: ImageFont.FreeTypeFont,
    text: str,
    x: int,
    y: int,
) -> None:
    if fixture.direction == "rtl":
        draw.text(
            (x, y),
            text,
            fill="black",
            font=font,
            anchor="ra",
            direction="rtl",
            language=fixture.language_tag,
        )
    else:
        draw.text(
            (x, y),
            text,
            fill="black",
            font=font,
            anchor="ra",
            direction="ltr",
        )


def _render(
    fixture: FixtureTemplate,
    font_path: Path,
    destination: Path,
) -> dict[str, object]:
    width, height = (1800, 900) if fixture.layout in {"columns", "form"} else (1800, 640)
    image = Image.new("RGB", (width, height), "white")
    font = ImageFont.truetype(str(font_path), 58)
    draw = ImageDraw.Draw(image)
    if fixture.layout == "columns":
        for index, line in enumerate(fixture.lines[:2]):
            _draw_line(draw, fixture, font, line, 1680, 180 + index * 150)
        for index, line in enumerate(fixture.lines[2:]):
            _draw_line(draw, fixture, font, line, 820, 180 + index * 150)
        draw.line((900, 90, 900, 600), fill="gray", width=2)
    else:
        for index, line in enumerate(fixture.lines):
            _draw_line(draw, fixture, font, line, 1680, 150 + index * 150)
            if fixture.layout == "form":
                top = 85 + index * 150
                draw.rectangle((100, top, 1700, top + 120), outline="gray", width=2)

    settings: dict[str, object] = {"name": fixture.transform}
    if fixture.transform == "blur":
        image = image.filter(ImageFilter.GaussianBlur(1.35))
        settings["radius"] = 1.35
    elif fixture.transform == "rotate":
        image = image.rotate(2.3, Image.Resampling.BICUBIC, fillcolor="white")
        settings["degrees"] = 2.3
    elif fixture.transform == "low_contrast":
        image = ImageEnhance.Contrast(image).enhance(0.42)
        settings["contrast_factor"] = 0.42
    elif fixture.transform == "phone":
        quad = (70, 45, width - 5, 5, width - 95, height - 15, 5, height - 70)
        image = image.transform(
            image.size,
            Image.Transform.QUAD,
            quad,
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        ).filter(ImageFilter.GaussianBlur(0.45))
        settings.update({"quad": list(quad), "blur_radius": 0.45})
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", compress_level=9)
    return settings


def _record(
    entry: Mapping[str, object],
    root: Path,
    private: bool,
) -> FixtureRecord:
    strings: dict[str, str] = {}
    for key in ("fixture_id", "language", "ground_truth", "image_path", "source_kind"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise OcrBenchmarkError(f"corpus fixture {key} must be a non-empty string")
        strings[key] = value
    relative = Path(strings["image_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise OcrBenchmarkError("corpus image_path must remain inside its corpus directory")
    image = (root / relative).resolve()
    if root.resolve() not in image.parents or not image.is_file():
        raise OcrBenchmarkError(f"corpus image is unavailable: {relative}")
    if image.stat().st_size > MAX_FIXTURE_BYTES:
        raise OcrBenchmarkError(f"corpus image exceeds {MAX_FIXTURE_BYTES} bytes")
    lines_value = entry.get("expected_lines")
    tags_value = entry.get("tags", [])
    if not isinstance(lines_value, list):
        raise OcrBenchmarkError("corpus expected_lines must contain non-empty strings")
    line_values = cast(list[object], lines_value)
    if not line_values or not all(
        isinstance(value, str) and value.strip() for value in line_values
    ):
        raise OcrBenchmarkError("corpus expected_lines must contain non-empty strings")
    if not isinstance(tags_value, list):
        raise OcrBenchmarkError("corpus tags must be strings")
    tag_values = cast(list[object], tags_value)
    if not all(isinstance(value, str) for value in tag_values):
        raise OcrBenchmarkError("corpus tags must be strings")
    if private and not all(
        isinstance(entry.get(key), str) and cast(str, entry[key]).strip()
        for key in ("lawful_basis", "generation_method")
    ):
        raise OcrBenchmarkError("private fixtures require lawful_basis and generation_method")
    return FixtureRecord(
        fixture_id=strings["fixture_id"],
        language=strings["language"],
        ground_truth=strings["ground_truth"],
        expected_lines=tuple(cast(list[str], line_values)),
        image_path=image,
        image_sha256=sha256_file(image),
        tags=tuple(cast(list[str], tag_values)),
        source_kind=strings["source_kind"],
        private=private,
    )


def fixture_record_from_entry(
    entry: Mapping[str, object],
    root: Path,
    private: bool,
) -> FixtureRecord:
    """Validate one corpus entry. Kept separate for focused tests."""

    return _record(entry, root, private)


def build_corpus(
    output_dir: Path,
    private_corpus: Path | None = None,
) -> dict[str, object]:
    """Generate public fixtures and append operator-attested private fixtures."""

    fonts = {role: _find_fonts(role) for role in FONT_ENV}
    entries: list[dict[str, object]] = []
    records: list[FixtureRecord] = []
    for fixture in PUBLIC_FIXTURES:
        font_path = _font_for(fonts, fixture)
        image = output_dir / "fixtures" / f"{fixture.fixture_id}.png"
        settings = _render(fixture, font_path, image)
        entry: dict[str, object] = {
            "fixture_id": fixture.fixture_id,
            "language": fixture.language,
            "ground_truth": "\n".join(fixture.lines),
            "expected_lines": list(fixture.lines),
            "image_path": image.relative_to(output_dir).as_posix(),
            "image_sha256": sha256_file(image),
            "tags": list(fixture.tags),
            "source_kind": "archiv-authored-synthetic",
            "generation_method": {
                "renderer": "Pillow",
                "layout": fixture.layout,
                "direction": fixture.direction,
                "language_tag": fixture.language_tag,
                "transformation": settings,
            },
            "font": {
                "role": fixture.font_role,
                "path": str(font_path),
                "sha256": sha256_file(font_path),
            },
            "private": False,
        }
        entries.append(entry)
        records.append(_record(entry, output_dir, False))

    if private_corpus is not None:
        private_root = private_corpus.expanduser().resolve()
        manifest_path = private_root / "manifest.json"
        if not manifest_path.is_file():
            raise OcrBenchmarkError(f"private corpus manifest not found: {manifest_path}")
        payload_value: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload_value, dict):
            raise OcrBenchmarkError("private corpus manifest must be an object")
        payload = cast(dict[str, object], payload_value)
        if payload.get("schema_version") != "1":
            raise OcrBenchmarkError("private corpus manifest schema_version must be '1'")
        fixtures_value = payload.get("fixtures")
        if not isinstance(fixtures_value, list):
            raise OcrBenchmarkError("private corpus fixtures must be a list")
        fixture_values = cast(list[object], fixtures_value)
        if len(fixture_values) > MAX_PRIVATE_FIXTURES:
            raise OcrBenchmarkError(
                f"private corpus fixtures must be a list of at most {MAX_PRIVATE_FIXTURES}"
            )
        for value in fixture_values:
            if not isinstance(value, dict):
                raise OcrBenchmarkError("private corpus fixture must be an object")
            entry = cast(dict[str, object], value)
            record = _record(entry, private_root, True)
            if any(item.fixture_id == record.fixture_id for item in records):
                raise OcrBenchmarkError(f"duplicate fixture_id: {record.fixture_id}")
            records.append(record)
            entries.append({**entry, "image_sha256": record.image_sha256, "private": True})

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "corpus": "archiv-multiengine-ocr",
        "licensing": ("Public phrases are Archiv-authored; local fonts are hashed but not copied."),
        "private_corpus_included": any(record.private for record in records),
        "fixtures": entries,
    }
    path = output_dir / "corpus.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_sha256"] = sha256_file(path)
    manifest["records"] = records
    return manifest
