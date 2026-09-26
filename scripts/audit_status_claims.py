#!/usr/bin/env python3
"""Audit documentation for a completion claim with no evidence behind it.

Pull requests #114-#121 marked ``docs/capability-expansion-plan.md`` "Status:
Implemented ... verified with comprehensive acceptance tests" while the measurements
those milestones themselves specified were never produced. This script makes that
class of mistake fail a check instead of sitting unnoticed in prose: any "Status:"
line in the documentation that claims a capability, milestone, or plan is finished
must have a concrete artefact — a test path, an auditor script, or a measured report
document — named nearby, in the same paragraph or blockquote.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

STATUS_LINE_PATTERN = re.compile(r"(?im)^\s*>?\s*\*{0,2}Status:\*{0,2}\s*(.+)$")
COMPLETION_WORD_PATTERN = re.compile(r"(?i)\b(implemented|done|complete(?:d)?|finished)\b")
ARTEFACT_PATTERN = re.compile(
    r"tests?/[\w./-]+\.py(?:::[\w:.\[\]-]+)?"  # a pytest node id or test file
    r"|scripts/[\w./-]+\.py"  # an auditor or measurement script
    r"|docs/[\w./-]*-report\.md"  # a measured report document
)


@dataclass(frozen=True, slots=True)
class Violation:
    doc: str
    line: int
    detail: str


def _markdown_files(root: Path) -> list[Path]:
    files: set[Path] = set((root / "docs").rglob("*.md")) if (root / "docs").is_dir() else set()
    readme = root / "README.md"
    if readme.is_file():
        files.add(readme)
    return sorted(files)


def _paragraph(lines: list[str], index: int) -> str:
    """The contiguous non-blank block containing ``lines[index]``.

    Blockquote and prose paragraphs in this repository's docs are separated by a
    blank line, so this captures a "Status:" line together with the sentences that
    justify it, without pulling in unrelated content above or below.
    """

    start = index
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = index
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return "\n".join(lines[start : end + 1])


def audit(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    docs = _markdown_files(root)
    if not docs:
        return [Violation("docs", 0, "no documentation files found")]

    for doc in docs:
        relative = doc.relative_to(root).as_posix()
        lines = doc.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            match = STATUS_LINE_PATTERN.match(line)
            if not match or not COMPLETION_WORD_PATTERN.search(match.group(1)):
                continue
            paragraph = _paragraph(lines, index)
            if not ARTEFACT_PATTERN.search(paragraph):
                violations.append(
                    Violation(
                        relative,
                        index + 1,
                        "claims completion with no test path, script, or measured-report "
                        "artefact named in the same paragraph",
                    )
                )
    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    root = arguments.root.resolve()
    violations = audit(root)
    payload = {
        "schema_version": "1",
        "ok": not violations,
        "violations": [asdict(item) for item in violations],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
