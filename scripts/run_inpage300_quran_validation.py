"""Compare private InPage300 extraction with pinned Quran references."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

from archiv.research.inpage_container import extract_inpage300, read_native_root_stream
from archiv.research.inpage_types import ExtractionError, sha256
from archiv.research.inpage_validation import compute_git_blob_sha1
from archiv.research.quran_reference import (
    QuranReference,
    compare_juz,
    parse_amrayn_json,
    parse_tanzil_xml,
    verses_for_juz,
)

INPAGE_REPOSITORY = "ShakesVision/html-experiments"
INPAGE_COMMIT = "1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a"
AMRAYN_REPOSITORY = "amrayn/quran-text"
AMRAYN_COMMIT = "d1868b249234f536c6048da69c272efc91ce44b4"
FORBIDDEN_CONTENT_KEYS = frozenset(
    {"payload", "decoded_text", "reference_text", "extracted_text"}
)

FIXTURES = (
    {
        "juz": 29,
        "path": "inpage/juz_29.inp",
        "git_blob_sha1": "b5c5774f41ea84a4b7ad6c859f0576da70604925",
        "file_sha256": "81c61955c2eb38fb14c100fdb36c642ee8e0f6d005109e894c24249617939ffa",
        "stream_sha256": "644ce2e08032d3ad914366dfce0561ca9e431a17429a538e1a49b97854fbf199",
        "stream_size": 410578,
    },
    {
        "juz": 30,
        "path": "inpage/juz_30.inp",
        "git_blob_sha1": "a797f2d3fd415bf757fbba1cc133bdad3ca58682",
        "file_sha256": "f1416c84af4fe716237d65d9d215d8a4e8ff874b1ee8122553d66d6399b3fe45",
        "stream_sha256": "0d17b9b6d5ea4332264e926c042f382f2d84a4d3d0ace0dfd1893f1208979d98",
        "stream_size": 543086,
    },
)

AMRAYN_PATH = "quran-full-tashkeel.json"
AMRAYN_BLOB_SHA1 = "ceccc426c01a7eef87383608efd8412064ad5cb0"


def _verified_git_file(path: Path, expected_blob_sha1: str) -> bytes:
    data = path.read_bytes()
    observed = compute_git_blob_sha1(data)
    if observed != expected_blob_sha1:
        raise ExtractionError(
            f"Git blob mismatch for {path}: expected {expected_blob_sha1}, got {observed}"
        )
    return data


def _assert_sanitized(value: object) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        for key, child in mapping.items():
            if isinstance(key, str) and key in FORBIDDEN_CONTENT_KEYS:
                raise ExtractionError(f"sanitized evidence contains forbidden key: {key}")
            _assert_sanitized(child)
    elif isinstance(value, list):
        for child in cast(list[object], value):
            _assert_sanitized(child)


def _reference_record(reference: QuranReference) -> dict[str, object]:
    return {
        "source_name": reference.source_name,
        "source_sha256": reference.source_sha256,
        "source_size": reference.source_size,
        "verse_count": len(reference.verses),
        "juz_29_count": len(verses_for_juz(reference, 29)),
        "juz_30_count": len(verses_for_juz(reference, 30)),
    }


def _measure_fixture(
    *,
    source_root: Path,
    fixture: dict[str, object],
    references: list[QuranReference],
) -> dict[str, object]:
    relative_path = str(fixture["path"])
    path = source_root / relative_path
    data = _verified_git_file(path, str(fixture["git_blob_sha1"]))
    file_sha = sha256(data)
    if file_sha != fixture["file_sha256"]:
        raise ExtractionError(
            f"file SHA-256 mismatch for {relative_path}: expected "
            f"{fixture['file_sha256']}, got {file_sha}"
        )
    stream = read_native_root_stream(path)
    if stream.variant != "300":
        raise ExtractionError(f"expected InPage300, got {stream.name}")
    if stream.stream_sha256 != fixture["stream_sha256"]:
        raise ExtractionError(
            f"stream SHA-256 mismatch for {relative_path}: expected "
            f"{fixture['stream_sha256']}, got {stream.stream_sha256}"
        )
    if stream.stream_size != fixture["stream_size"]:
        raise ExtractionError(
            f"stream size mismatch for {relative_path}: expected "
            f"{fixture['stream_size']}, got {stream.stream_size}"
        )

    first_metrics, first_text = extract_inpage300(stream)
    second_metrics, second_text = extract_inpage300(stream)
    first_text_sha = sha256(first_text.encode("utf-8"))
    if asdict(first_metrics) != asdict(second_metrics):
        raise ExtractionError("repeated extraction metrics are not deterministic")
    if first_text_sha != sha256(second_text.encode("utf-8")):
        raise ExtractionError("repeated private text is not deterministic")
    if first_metrics.text_emitted or first_metrics.native_support_claimed:
        raise ExtractionError("research extraction violated its support boundary")

    juz = int(fixture["juz"])
    comparisons = {
        reference.source_name: asdict(compare_juz(first_text, reference, juz))
        for reference in references
    }
    return {
        "juz": juz,
        "source_path": relative_path,
        "git_blob_sha1": fixture["git_blob_sha1"],
        "file_sha256": file_sha,
        "file_size": len(data),
        "stream_name": stream.name,
        "stream_sha256": stream.stream_sha256,
        "stream_size": stream.stream_size,
        "private_text_sha256": first_text_sha,
        "extraction_metrics": asdict(first_metrics),
        "comparisons": comparisons,
        "deterministic_repetition": True,
        "text_emitted": False,
        "native_support_claimed": False,
    }


def build_evidence(
    *,
    inpage_root: Path,
    amrayn_root: Path,
    tanzil_xml: Path | None,
    archiv_head: str,
) -> dict[str, object]:
    amrayn_data = _verified_git_file(amrayn_root / AMRAYN_PATH, AMRAYN_BLOB_SHA1)
    references = [parse_amrayn_json(amrayn_data)]
    if tanzil_xml is not None:
        references.append(parse_tanzil_xml(tanzil_xml.read_bytes()))

    fixtures = [
        _measure_fixture(
            source_root=inpage_root,
            fixture=dict(fixture),
            references=references,
        )
        for fixture in FIXTURES
    ]
    return {
        "schema_version": 1,
        "archiv_head": archiv_head,
        "scope": "research-only InPage300 Quran text comparison",
        "inpage_source": {
            "repository": INPAGE_REPOSITORY,
            "commit": INPAGE_COMMIT,
        },
        "references": {
            "amrayn": {
                "repository": AMRAYN_REPOSITORY,
                "commit": AMRAYN_COMMIT,
                "git_blob_sha1": AMRAYN_BLOB_SHA1,
                "license": "MIT",
                **_reference_record(references[0]),
            },
            **(
                {
                    "tanzil": {
                        "publisher": "Tanzil Project",
                        "declared_version": "1.1",
                        "license": ("Creative Commons Attribution 3.0 with Tanzil terms"),
                        **_reference_record(references[1]),
                    }
                }
                if len(references) == 2
                else {}
            ),
        },
        "fixtures": fixtures,
        "privacy": {
            "fixture_bytes_uploaded": False,
            "reference_text_uploaded": False,
            "decoded_text_printed_or_uploaded": False,
            "legacy_inpage_executed": False,
            "online_converter_used": False,
        },
        "native_support_claimed": False,
        "layout_support_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inpage-root", type=Path, required=True)
    parser.add_argument("--amrayn-root", type=Path, required=True)
    parser.add_argument("--tanzil-xml", type=Path)
    parser.add_argument("--archiv-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build_evidence(
        inpage_root=args.inpage_root,
        amrayn_root=args.amrayn_root,
        tanzil_xml=args.tanzil_xml,
        archiv_head=args.archiv_head,
    )
    _assert_sanitized(evidence)
    serialized = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    serialized.encode("ascii")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="ascii")
    print(f"Wrote sanitized comparisons for {len(FIXTURES)} InPage300 candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
