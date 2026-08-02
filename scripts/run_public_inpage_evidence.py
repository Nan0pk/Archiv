"""Produce sanitized native InPage research evidence from already checked-out sources."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping

from archiv.research.inpage_container import extract_inpage300, read_native_root_stream
from archiv.research.inpage_legacy import (
    SPECIAL_ESCAPES,
    compare_mappings,
    extract_inpage100,
    parse_mapping_xml,
)
from archiv.research.inpage_types import (
    ExtractionError,
    MappingTable,
    RootStream,
    TextMetrics,
    sha256,
)
from archiv.research.inpage_validation import compute_git_blob_sha1

SOURCE_REPOSITORY = "ShakesVision/html-experiments"
SOURCE_COMMIT = "1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a"
C_REPOSITORY = "KamalAbdali/InpageToUnicode"
C_COMMIT = "6eab0278d3717de98c230712121e4460266755b8"


@dataclass(frozen=True)
class FixtureSpec:
    source_path: str
    blob_sha1: str
    variant: str
    required: bool
    file_sha256: str | None = None
    expected_stream_sha256: str | None = None
    expected_stream_size: int | None = None
    expected_details: Mapping[str, int] = field(default_factory=dict)


FIXTURES = (
    FixtureSpec(
        source_path="inpage/juz_29.inp",
        blob_sha1="b5c5774f41ea84a4b7ad6c859f0576da70604925",
        file_sha256="81c61955c2eb38fb14c100fdb36c642ee8e0f6d005109e894c24249617939ffa",
        variant="300",
        required=True,
        expected_stream_sha256="644ce2e08032d3ad914366dfce0561ca9e431a17429a538e1a49b97854fbf199",
        expected_stream_size=410578,
        expected_details={
            "utf16_code_units": 205289,
            "arabic_code_units": 45511,
            "ascii_printable_code_units": 16636,
            "zero_code_units": 65862,
            "longest_allowed_run_code_units": 54655,
        },
    ),
    FixtureSpec(
        source_path="inpage/juz_30.inp",
        blob_sha1="a797f2d3fd415bf757fbba1cc133bdad3ca58682",
        variant="300",
        required=False,
    ),
    FixtureSpec(
        source_path="inpage/khali hathely (1).inp",
        blob_sha1="c7f69058ca0024be6866531429292967ad852ef1",
        file_sha256="a3c6a60de0057345849529213d7a216a4f7d9b278434db883597e311ec1ab276",
        variant="100",
        required=True,
        expected_stream_sha256="a3bb14053cb2cb155a4caa282b6e49af196379d96b084d1dec39646b7d312e7c",
        expected_stream_size=378229,
        expected_details={
            "all_04_escape_pairs": 21117,
            "plausible_u32_length_records": 2253,
            "plausible_record_payload_bytes": 110024,
            "04_escape_pairs_inside_plausible_records": 20290,
        },
    ),
    FixtureSpec(
        source_path="inpage/Urdu Grammer Book.inp",
        blob_sha1="34697e74551db4039cd4f0b39902cb6ccfc91e56",
        variant="100",
        required=False,
    ),
    FixtureSpec(
        source_path="inpage/Zakiya Mashhadi.inp",
        blob_sha1="715fe2c75a23b41a8de987ba42ea5913ca4f5aa1",
        variant="100",
        required=False,
    ),
)

SOURCE_FILES = (
    ("LICENSE", "LICENSE", "3eeb87abc1249472a615b87cec629164d01be9f5"),
    ("NOTICE", "NOTICE", "6017c85ce73750fd8157271aa93319af20085b76"),
    (
        "InpageToUni.xml",
        "inpage/pdf-texter/UnicodeToInpage/InpageToUni.xml",
        "0407540a83d1ae2738c7c1879b25419d010715ee",
    ),
)
C_SOURCE = (
    "InpToUni.c",
    "src/InpToUni.c",
    "1d40d64c00f3e543d4cc5c2de2674cdab13a0983",
)


def _verified_file(path: Path, expected_blob_sha1: str) -> tuple[bytes, dict[str, object]]:
    data = path.read_bytes()
    observed_blob = compute_git_blob_sha1(data)
    if observed_blob != expected_blob_sha1:
        raise ExtractionError(
            f"Git blob mismatch for {path}: expected {expected_blob_sha1}, got {observed_blob}"
        )
    return data, {
        "git_blob_sha1": observed_blob,
        "sha256": sha256(data),
        "size": len(data),
    }


def _parse_c_mapping(data: bytes) -> MappingTable:
    if len(data) > 1024 * 1024:
        raise ExtractionError("C mapping source size limit exceeded")
    text = data.decode("utf-8")
    match = re.search(
        r"unsigned\s+char\s+unicodebyte\s*\[\s*256\s*\]\s*=\s*\{(.*?)\};",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise ExtractionError("unicodebyte[256] array not found in pinned C source")
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    tokens = re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", body)
    values = [int(token, 0) for token in tokens]
    if len(values) != 256 or any(not 0 <= value <= 255 for value in values):
        raise ExtractionError(f"expected 256 bounded C mapping values, got {len(values)}")
    mapping = {
        code: chr(0x0600 + value)
        for code, value in enumerate(values)
        if code not in SPECIAL_ESCAPES
    }
    return MappingTable(
        source_sha256=sha256(data),
        source_size=len(data),
        mapping_count=len(mapping),
        duplicates=0,
        conflicts=0,
        ignored=len(SPECIAL_ESCAPES),
        values=mapping,
    )


def _extract(stream: RootStream, xml_mapping: MappingTable) -> tuple[TextMetrics, str]:
    if stream.variant == "300":
        return extract_inpage300(stream)
    if stream.variant == "100":
        return extract_inpage100(stream, mapping=xml_mapping)
    raise ExtractionError(f"unsupported research stream variant: {stream.variant}")


def _measure_fixture(
    source_root: Path, spec: FixtureSpec, xml_mapping: MappingTable
) -> dict[str, object]:
    path = source_root / spec.source_path
    data, identity = _verified_file(path, spec.blob_sha1)
    observed_file_sha = sha256(data)
    if spec.file_sha256 is not None and observed_file_sha != spec.file_sha256:
        raise ExtractionError(
            f"SHA-256 mismatch for {spec.source_path}: expected {spec.file_sha256}, "
            f"got {observed_file_sha}"
        )
    record: dict[str, object] = {
        "source_path": spec.source_path,
        "git_blob_sha1": identity["git_blob_sha1"],
        "file_sha256": observed_file_sha,
        "file_size": len(data),
        "expected_variant": spec.variant,
    }
    try:
        stream = read_native_root_stream(path)
        if stream.variant != spec.variant:
            raise ExtractionError(f"expected InPage{spec.variant}, got {stream.name}")
        first_metrics, first_text = _extract(stream, xml_mapping)
        second_metrics, second_text = _extract(stream, xml_mapping)
        if asdict(first_metrics) != asdict(second_metrics):
            raise ExtractionError("repeated sanitized metrics are not deterministic")
        if sha256(first_text.encode("utf-8")) != sha256(second_text.encode("utf-8")):
            raise ExtractionError("repeated private text hash is not deterministic")
        if first_metrics.native_support_claimed or first_metrics.text_emitted:
            raise ExtractionError("research result violated its support/privacy boundary")
        if (
            spec.expected_stream_sha256 is not None
            and stream.stream_sha256 != spec.expected_stream_sha256
        ):
            raise ExtractionError(
                f"stream SHA-256 mismatch: expected {spec.expected_stream_sha256}, "
                f"got {stream.stream_sha256}"
            )
        if (
            spec.expected_stream_size is not None
            and stream.stream_size != spec.expected_stream_size
        ):
            raise ExtractionError(
                f"stream size mismatch: expected {spec.expected_stream_size}, "
                f"got {stream.stream_size}"
            )
        for key, expected in spec.expected_details.items():
            observed = first_metrics.details.get(key)
            if observed != expected:
                raise ExtractionError(f"{key}: expected {expected}, got {observed}")
        record.update(
            {
                "status": "measured",
                "stream_name": stream.name,
                "stream_sha256": stream.stream_sha256,
                "stream_size": stream.stream_size,
                "sector_size": stream.sector_size,
                "sector_count": stream.sector_count,
                "deterministic_repetition": True,
                "metrics": asdict(first_metrics),
            }
        )
    except (AssertionError, ExtractionError, OSError, ValueError) as error:
        if spec.required:
            raise
        record.update(
            {
                "status": "bounded_failure",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
    return record


def build_evidence(
    *, source_root: Path, c_source_root: Path, archiv_head: str
) -> dict[str, object]:
    source_measurements: dict[str, object] = {}
    source_data: dict[str, bytes] = {}
    for name, relative_path, expected_blob in SOURCE_FILES:
        data, measurement = _verified_file(source_root / relative_path, expected_blob)
        source_data[name] = data
        source_measurements[name] = measurement
    c_name, c_relative_path, c_blob = C_SOURCE
    c_data, c_measurement = _verified_file(c_source_root / c_relative_path, c_blob)
    source_measurements[c_name] = c_measurement

    xml_mapping = parse_mapping_xml(source_data["InpageToUni.xml"])
    c_mapping = _parse_c_mapping(c_data)
    comparison = compare_mappings(xml_mapping, c_mapping)
    fixtures = [_measure_fixture(source_root, spec, xml_mapping) for spec in FIXTURES]

    return {
        "schema_version": 1,
        "archiv_head": archiv_head,
        "scope": "bounded non-redistributing research extraction from pinned public native candidates",
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "source_license": (
            "Attribution Required License 1.0; fixture-level literary provenance remains incomplete"
        ),
        "secondary_mapping_repository": C_REPOSITORY,
        "secondary_mapping_commit": C_COMMIT,
        "secondary_mapping_license_status": (
            "no clear repository licence found; parsed as research evidence only"
        ),
        "source_files": source_measurements,
        "mapping_comparison": {
            "xml": {
                "mapping_count": xml_mapping.mapping_count,
                "duplicates": xml_mapping.duplicates,
                "conflicts": xml_mapping.conflicts,
                "ignored": xml_mapping.ignored,
            },
            "c_default": {
                "mapping_count": c_mapping.mapping_count,
                "special_cases_excluded": c_mapping.ignored,
            },
            **asdict(comparison),
        },
        "fixtures": fixtures,
        "privacy": {
            "fixture_bytes_uploaded": False,
            "decoded_text_printed_or_uploaded": False,
            "third_party_parser_executed": False,
            "legacy_inpage_executed": False,
            "online_converter_used": False,
            "mapping_sources_executed": False,
        },
        "native_support_claimed": False,
        "layout_support_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--c-source-root", type=Path, required=True)
    parser.add_argument("--archiv-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build_evidence(
        source_root=args.source_root,
        c_source_root=args.c_source_root,
        archiv_head=args.archiv_head,
    )
    serialized = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    serialized.encode("ascii")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="ascii")
    print(f"Wrote sanitized evidence for {len(FIXTURES)} pinned candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
