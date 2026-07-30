"""Create a tiny synthetic cross-file vault available after package installation."""

from __future__ import annotations

import json
from pathlib import Path

from archiv.hashing import sha256_bytes

SAMPLE_FILES: dict[str, str] = {
    "operations.txt": (
        "Archiv offline alpha operations note.\n"
        "The unique fixture marker links this source to the other sample records.\n"
        "Operational finding: canonical originals must remain unchanged.\n"
    ),
    "research.md": (
        "# Offline research note\n\n"
        "The unique fixture marker is deliberately repeated across files.\n"
        "Research finding: local citations must resolve without network access.\n"
    ),
    "decision.txt": (
        "Archiv decision record\n"
        "The unique fixture marker identifies the bounded alpha demonstration.\n"
        "Decision: validators, not model prose, determine success.\n"
    ),
}


def create_sample_vault(output: Path, *, force: bool = False) -> Path:
    """Write deterministic public-safe sample files and a hash manifest."""

    output = output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise NotADirectoryError("sample vault destination must be a directory")
    if output.exists() and any(output.iterdir()):
        if not force:
            raise FileExistsError(
                "sample vault destination is not empty; pass --force to replace it"
            )
        for child in output.iterdir():
            if child.is_dir():
                raise ValueError("--force refuses to delete nested directories")
            child.unlink()
    output.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for name, text in sorted(SAMPLE_FILES.items()):
        path = output / name
        path.write_text(text, encoding="utf-8")
        data = text.encode("utf-8")
        entries.append({"path": name, "bytes": len(data), "sha256": sha256_bytes(data)})
    manifest = {
        "schema_version": "1",
        "kind": "archiv-synthetic-sample-vault",
        "query": "unique fixture marker",
        "entries": entries,
    }
    (output / "sample-vault-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
