"""Isolated optional RapidOCR benchmark adapter; not part of production ingestion."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from archiv.hashing import sha256_file


class _RapidOutput(Protocol):
    txts: tuple[str, ...] | None
    scores: tuple[float, ...] | None
    boxes: object | None


class _RapidEngine(Protocol):
    def __call__(self, image: Path) -> _RapidOutput: ...


class _RapidEngineFactory(Protocol):
    def __call__(self, *, params: Mapping[str, object]) -> _RapidEngine: ...


class _LangRec(Protocol):
    ARABIC: object


class _ModelType(Protocol):
    MOBILE: object


class _OcrVersion(Protocol):
    PPOCRV5: object


class _EngineType(Protocol):
    ONNXRUNTIME: object


@runtime_checkable
class _ArrayLike(Protocol):
    def tolist(self) -> object: ...


def _directory_evidence(path: Path) -> dict[str, object]:
    files = sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else []
    return {
        "root": str(path),
        "bytes": sum(item.stat().st_size for item in files),
        "files": [
            {
                "relative_path": item.relative_to(path).as_posix(),
                "bytes": item.stat().st_size,
                "sha256": sha256_file(item),
            }
            for item in files
        ],
    }


def _run_engine(request_path: Path, response_path: Path) -> None:
    request_value: object = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request_value, dict):
        raise ValueError("request must be an object")
    request = cast(dict[str, object], request_value)
    fixtures_value = request.get("fixtures")
    model_root_value = request.get("model_root")
    if not isinstance(fixtures_value, list) or not isinstance(model_root_value, str):
        raise ValueError("request fixtures/model_root are invalid")
    fixtures = cast(list[object], fixtures_value)
    model_root = Path(model_root_value).resolve()
    model_root.mkdir(parents=True, exist_ok=True)

    module = importlib.import_module("rapidocr")
    onnxruntime_module = importlib.import_module("onnxruntime")
    engine_factory = cast(_RapidEngineFactory, module.RapidOCR)
    lang_rec = cast(_LangRec, module.LangRec)
    model_type = cast(_ModelType, module.ModelType)
    ocr_version = cast(_OcrVersion, module.OCRVersion)
    engine_type = cast(_EngineType, module.EngineType)
    engine = engine_factory(
        params={
            "Global.model_root_dir": model_root,
            "Rec.lang_type": lang_rec.ARABIC,
            "Rec.model_type": model_type.MOBILE,
            "Rec.ocr_version": ocr_version.PPOCRV5,
            "Rec.engine_type": engine_type.ONNXRUNTIME,
        }
    )

    results: list[dict[str, object]] = []
    for value in fixtures:
        if not isinstance(value, dict):
            raise ValueError("fixture request must be an object")
        fixture = cast(dict[str, object], value)
        fixture_id = fixture.get("fixture_id")
        image_path = fixture.get("image_path")
        if not isinstance(fixture_id, str) or not isinstance(image_path, str):
            raise ValueError("fixture request is invalid")
        output = engine(Path(image_path))
        texts = list(output.txts or ())
        scores = list(output.scores or ())
        boxes = output.boxes
        coordinates = boxes.tolist() if isinstance(boxes, _ArrayLike) else boxes
        results.append(
            {
                "fixture_id": fixture_id,
                "text": "\n".join(texts),
                "lines": texts,
                "confidence": scores,
                "coordinates": coordinates,
            }
        )

    package_file = module.__file__
    runtime_file = onnxruntime_module.__file__
    if not isinstance(package_file, str) or not isinstance(runtime_file, str):
        raise RuntimeError("RapidOCR package paths are unavailable")
    package_root = Path(package_file).resolve().parent
    runtime_root = Path(runtime_file).resolve().parent
    response = {
        "status": "succeeded",
        "results": results,
        "evidence": {
            "package_root": str(package_root),
            "package_footprint": _directory_evidence(package_root),
            "onnxruntime_footprint": _directory_evidence(runtime_root),
            "offline_after_materialization": True,
            "coordinates_available": True,
            "confidence_available": True,
        },
        "models": {
            "license_notice": (
                "RapidOCR states OCR model copyright is held by Baidu; Archiv does not "
                "redistribute weights."
            ),
            "model_footprint": _directory_evidence(model_root),
        },
    }
    response_path.write_text(
        json.dumps(response, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(request_path: Path, response_path: Path) -> None:
    """Run RapidOCR while keeping download chatter separate from failure evidence."""

    diagnostic_path = response_path.with_name("rapidocr-adapter.log")
    try:
        with (
            diagnostic_path.open("w", encoding="utf-8") as diagnostic,
            contextlib.redirect_stdout(diagnostic),
            contextlib.redirect_stderr(diagnostic),
        ):
            _run_engine(request_path, response_path)
    except Exception as error:  # noqa: BLE001 - optional engine failures need exact evidence
        with diagnostic_path.open("a", encoding="utf-8") as diagnostic:
            traceback.print_exc(file=diagnostic)
        raise RuntimeError(f"RapidOCR adapter failed: {type(error).__name__}: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.request, args.response)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
