"""Isolated optional RapidOCR benchmark adapter; not part of production ingestion."""

# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Protocol, cast

from archiv.hashing import sha256_file


class _RapidOutput(Protocol):
    txts: tuple[str, ...] | None
    scores: tuple[float, ...] | None
    boxes: object | None


class _RapidEngine(Protocol):
    def __call__(self, image: Path) -> _RapidOutput: ...


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


def run(request_path: Path, response_path: Path) -> None:
    request_value: object = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request_value, dict):
        raise ValueError("request must be an object")
    request = cast(dict[str, object], request_value)
    fixtures = request.get("fixtures")
    model_root_value = request.get("model_root")
    if not isinstance(fixtures, list) or not isinstance(model_root_value, str):
        raise ValueError("request fixtures/model_root are invalid")
    model_root = Path(model_root_value).resolve()
    model_root.mkdir(parents=True, exist_ok=True)

    module = importlib.import_module("rapidocr")
    onnxruntime_module = importlib.import_module("onnxruntime")
    rapidocr = cast(object, getattr(module, "RapidOCR"))
    lang_rec = cast(object, getattr(module, "LangRec"))
    model_type = cast(object, getattr(module, "ModelType"))
    ocr_version = cast(object, getattr(module, "OCRVersion"))
    engine_type = cast(object, getattr(module, "EngineType"))
    engine_factory = cast(type[_RapidEngine], rapidocr)
    engine = engine_factory(
        params={
            "Global.model_root_dir": model_root,
            "Rec.lang_type": getattr(lang_rec, "ARABIC"),
            "Rec.model_type": getattr(model_type, "MOBILE"),
            "Rec.ocr_version": getattr(ocr_version, "PPOCRV5"),
            "Rec.engine_type": getattr(engine_type, "ONNXRUNTIME"),
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
        coordinates = output.boxes.tolist() if hasattr(output.boxes, "tolist") else output.boxes
        results.append(
            {
                "fixture_id": fixture_id,
                "text": "\n".join(texts),
                "lines": texts,
                "confidence": scores,
                "coordinates": coordinates,
            }
        )

    package_root = Path(module.__file__).resolve().parent
    runtime_root = Path(onnxruntime_module.__file__).resolve().parent
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    run(args.request, args.response)


if __name__ == "__main__":
    main()
