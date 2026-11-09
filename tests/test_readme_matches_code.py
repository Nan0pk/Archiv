"""The README must stay true to the code.

Two blocks are written from the program itself (the file-type table and the command list, by
``scripts/generate_readme_blocks.py``); this test fails when either has fallen behind. The rest
is checked mechanically where it can be: the version and Python badges, every link, and every
``archiv`` command the README names. It does not judge the prose, so
a change in behaviour still needs the README's sentences read by a person.
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS_WITH_LINKS = [ROOT / "README.md"]


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_readme_blocks", ROOT / "scripts" / "generate_readme_blocks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    return _load_generator()


def _project() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_generated_blocks_are_up_to_date(generator: ModuleType) -> None:
    stale = [path.relative_to(ROOT) for path in generator.out_of_date()]
    assert not stale, (
        f"{stale} no longer match the code. Run: python scripts/generate_readme_blocks.py"
    )


def test_version_and_python_badges_match_pyproject() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    version = str(_project()["version"])
    assert f"badge/version-{version}-" in readme, f"the version badge is not {version}"
    requires = str(_project()["requires-python"])
    assert requires.startswith(">=")
    assert f"badge/python-{requires[2:]}%2B-" in readme, f"the Python badge is not {requires}"


def _slug(heading: str) -> str:
    """How GitHub turns a heading into an anchor."""
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(text: str) -> set[str]:
    without_code = re.sub(r"```.*?```", "", text, flags=re.S)
    return {
        _slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", without_code, flags=re.M)
    }


@pytest.mark.parametrize("doc", DOCS_WITH_LINKS, ids=lambda p: p.name)
def test_every_link_resolves(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    broken: list[str] = []
    for match in re.finditer(r"\]\(([^)\s]+)\)", prose):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path_part, _, anchor = target.partition("#")
        file = doc if not path_part else (doc.parent / path_part)
        if not file.exists() or (
            anchor
            and file.suffix == ".md"
            and anchor not in _anchors(file.read_text(encoding="utf-8"))
        ):
            broken.append(target)
    assert not broken, f"{doc.name} has links that lead nowhere: {sorted(set(broken))}"


def _mentioned_commands(text: str) -> set[str]:
    """Every ``archiv ...`` invocation in inline code or a code block."""
    found: set[str] = set()
    for match in re.finditer(r"`archiv ([^`\n]+)`", text):
        found.add(match.group(1))
    for match in re.finditer(r"^\s*\$?\s*archiv (.+)$", text, flags=re.M):
        found.add(match.group(1))
    return found


def _resolve(tree: object, words: list[str]) -> str | None:
    """The unknown word that stops the walk, or None if the words name real commands."""
    current = tree
    for word in words:
        commands = getattr(current, "commands", None)
        if commands is None:
            return None  # a leaf: the rest are arguments and options
        if word.startswith(("-", "$", '"', "'", "~", "<")) or word.isupper():
            return None  # an option or a placeholder
        if word not in commands:
            return word
        current = commands[word]
    return None


@pytest.mark.parametrize("doc", DOCS_WITH_LINKS, ids=lambda p: p.name)
def test_every_command_named_exists(doc: Path, generator: ModuleType) -> None:
    tree = generator._command_tree()  # pyright: ignore[reportPrivateUsage]
    unknown: list[str] = []
    for invocation in sorted(_mentioned_commands(doc.read_text(encoding="utf-8"))):
        words = invocation.split()
        # `archiv COMMAND --help`, `archiv TYPE` style placeholders are not commands.
        if not words or words[0].isupper():
            continue
        bad = _resolve(tree, words)
        if bad is not None:
            unknown.append(f"archiv {invocation}  (no such command: {bad})")
    assert not unknown, f"{doc.name} names commands that do not exist: {unknown}"


def test_the_readme_lists_every_visible_command(generator: ModuleType) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [path for path in generator.visible_commands() if f"`archiv {path}" not in readme]
    assert not missing, f"README.md does not list: {missing}"
