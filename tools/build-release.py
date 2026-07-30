#!/usr/bin/env python3
"""Build reproducible alpha artifacts, checksums, and a minimal CycloneDX SBOM."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

EPOCH = 1767225600


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tracked_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    return [root / item.decode() for item in completed.stdout.split(b"\0") if item]


def source_archive(root: Path, output: Path, version: str) -> None:
    prefix = f"archiv-core-{version}"
    with tempfile.TemporaryDirectory(prefix="archiv-source-") as directory:
        tar_path = Path(directory) / "source.tar"
        with tarfile.open(tar_path, "w") as archive:
            for path in tracked_files(root):
                if not path.is_file():
                    continue
                relative = path.relative_to(root)
                info = archive.gettarinfo(str(path), arcname=f"{prefix}/{relative.as_posix()}")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = EPOCH
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        with tar_path.open("rb") as source, output.open("wb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)


def requirement_name(value: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", value)
    if match is None:
        raise ValueError(f"cannot parse dependency: {value}")
    return match.group(0)


def write_sbom(root: Path, output: Path, version: str) -> None:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = [requirement_name(value) for value in project["dependencies"]]
    components = [
        {
            "type": "library",
            "name": name,
            "bom-ref": f"pkg:pypi/{name.lower()}",
        }
        for name in sorted(dependencies, key=str.lower)
    ]
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000001",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "archiv-core",
                "version": version,
                "bom-ref": f"pkg:pypi/archiv-core@{version}",
            }
        },
        "components": components,
        "dependencies": [
            {
                "ref": f"pkg:pypi/archiv-core@{version}",
                "dependsOn": [component["bom-ref"] for component in components],
            }
        ],
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_wheel(root: Path, output: Path) -> Path:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(EPOCH)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output),
            str(root),
        ],
        check=True,
        env=environment,
    )
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one wheel")
    return wheels[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/release"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = str(project["version"])

    with tempfile.TemporaryDirectory(prefix="archiv-wheel-a-") as a, tempfile.TemporaryDirectory(
        prefix="archiv-wheel-b-"
    ) as b:
        first = build_wheel(root, Path(a))
        second = build_wheel(root, Path(b))
        if digest(first) != digest(second):
            raise RuntimeError("wheel build is not reproducible")
        wheel = output / first.name
        shutil.copyfile(first, wheel)

    source = output / f"archiv-core-{version}.tar.gz"
    source_archive(root, source, version)
    sbom = output / "archiv-core.cdx.json"
    write_sbom(root, sbom, version)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest = output / "release-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "version": version,
                "source_commit": commit,
                "source_date_epoch": EPOCH,
                "reproducible_wheel": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = sorted(path for path in output.iterdir() if path.is_file())
    checksums = "".join(f"{digest(path)}  {path.name}\n" for path in artifacts)
    (output / "SHA256SUMS").write_text(checksums, encoding="utf-8")


if __name__ == "__main__":
    main()
