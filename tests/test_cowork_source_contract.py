"""Generated source snapshots pin the boundary checked before the real transport run."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "archiv_cowork_verifier", ROOT / "scripts/verify_cowork_regression.py"
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
CONNECTION = "src/electron/mcp/client/MCPServerConnection.ts"
MANAGER = "src/electron/mcp/client/MCPClientManager.ts"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _snapshot(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "cowork"
    root.mkdir()
    files = {
        "package.json": json.dumps({"version": "0.0.0", "engines": {"node": ">=24.0.0"}}),
        "src/electron/mcp/types.ts": "\n".join(
            (
                'export type MCPTransportType = "stdio"',
                "command?: string",
                "args?: string[]",
                "env?: Record<string, string>",
                "cwd?: string",
                "connectionTimeout?: number",
                "requestTimeout?: number",
                "isError?: boolean",
            )
        ),
        "src/cli/direct-run.ts": "\n".join(
            (
                'case "mcp-add"',
                'case "mcp-test"',
                'transport === "stdio"',
                "command: commandParts[0]",
                "args: commandParts.slice(1)",
                "cwd: path.resolve(args.cwd)",
            )
        ),
        "src/electron/mcp/client/transports/StdioTransport.ts": "\n".join(
            (
                "...process.env",
                "...env",
                "cwd: cwd || process.cwd()",
                "env: processEnv",
                'stdio: ["pipe", "pipe", "pipe"]',
                "sendRequest(",
            )
        ),
        CONNECTION: "\n".join(
            (
                'const PROTOCOL_VERSION = "2024-11-05";',
                "MCP_METHODS.INITIALIZE",
                "MCP_METHODS.TOOLS_LIST",
                "MCP_METHODS.TOOLS_CALL",
                "return result as MCPCallResult",
            )
        ),
        MANAGER: (
            "class Manager {\n  async callTool(toolName, args) {\n"
            "    return await connection.callTool(toolName, args)\n  }\n"
            "  async testServer() {\n    return { tools: toolCount };\n  }\n}\n"
        ),
        "docs/cli.md": (
            "cowork mcp list|add|remove|enable|disable|test\ndo not require a Control Plane token"
        ),
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "repository": "generated-fixture",
                "revision": _git(root, "rev-parse", "HEAD"),
                "version": "0.0.0",
                "protocol_version": "2024-11-05",
                "source_blobs": {name: _git(root, "hash-object", name) for name in files},
            }
        ),
        encoding="utf-8",
    )
    return root, lock


def _check(root: Path, lock: Path, mode: str) -> tuple[int, dict[str, Any]]:
    output = root.parent / "source-report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_cowork_source_contract.py"),
            "--cowork-root",
            str(root),
            "--lock",
            str(lock),
            "--mode",
            mode,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(output.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mode", ("pinned", "current"))
def test_locked_classic_source_still_passes(tmp_path: Path, mode: str) -> None:
    root, lock = _snapshot(tmp_path)
    code, report = _check(root, lock, mode)
    assert code == 0 and report["compatible"] is True
    assert report["protocol_version"] == "2024-11-05"
    assert report["upstream_adopted"] is False


def test_current_source_uses_its_protocol_and_accepts_forwarded_options(tmp_path: Path) -> None:
    root, lock = _snapshot(tmp_path)
    connection = root / CONNECTION
    connection.write_text(connection.read_text().replace("2024-11-05", "2025-06-18"))
    manager = root / MANAGER
    manager.write_text(
        manager.read_text().replace(
            "connection.callTool(toolName, args)",
            "connection.callTool(this.toolServerNames.get(toolName) ?? toolName, args, { signal })",
        )
    )
    locked_bytes = lock.read_bytes()
    code, report = _check(root, lock, "current")
    assert code == 0 and report["compatible"] is True
    assert report["protocol_version"] == "2025-06-18"
    assert lock.read_bytes() == locked_bytes
    code, report = _check(root, lock, "pinned")
    assert code != 0 and report["compatible"] is False
    assert any(
        check["name"] == "pinned_protocol_version" and not check["passed"]
        for check in report["checks"]
    )


@pytest.mark.parametrize(
    "declaration",
    (
        'const MODERN_PROTOCOL_VERSION = "2026-07-28";',
        'const PROTOCOL_VERSION = "unknown";',
        'const PROTOCOL_VERSION = "2025-06-18"; const PROTOCOL_VERSION = "2024-11-05";',
    ),
)
def test_missing_malformed_or_ambiguous_protocol_fails_closed(
    tmp_path: Path, declaration: str
) -> None:
    root, lock = _snapshot(tmp_path)
    connection = root / CONNECTION
    connection.write_text(
        connection.read_text().replace('const PROTOCOL_VERSION = "2024-11-05";', declaration)
    )
    code, report = _check(root, lock, "current")
    assert code != 0 and report["compatible"] is False
    assert report["protocol_version"] is None


@pytest.mark.parametrize(
    ("requested", "negotiated", "source", "mode", "expected"),
    (
        ("2024-11-05", "2024-11-05", "2024-11-05", "pinned", True),
        ("2025-06-18", "2025-06-18", "2025-06-18", "current", True),
        ("2025-06-18", "2024-11-05", "2025-06-18", "current", False),
        ("2024-11-05", "2024-11-05", "2025-06-18", "current", False),
        ("2025-06-18", "2025-06-18", "2025-06-18", "pinned", False),
        (None, None, None, "current", False),
    ),
)
def test_verifier_rejects_missing_or_mismatched_protocol_evidence(
    requested: str | None,
    negotiated: str | None,
    source: str | None,
    mode: str,
    expected: bool,
) -> None:
    probe: dict[str, object] = {
        "requested_protocol_version": requested,
        "negotiated_protocol_version": negotiated,
    }
    assert (
        VERIFIER.protocol_matches_source(
            probe, {"protocol_version": source}, {"protocol_version": "2024-11-05"}, mode
        )
        is expected
    )


@pytest.mark.parametrize(
    "call",
    (
        "await connection.callTool(toolName, args)",
        "return await connection.callTool(toolName, {})",
    ),
)
def test_manager_must_return_the_tool_result_and_forward_arguments(
    tmp_path: Path, call: str
) -> None:
    root, lock = _snapshot(tmp_path)
    manager = root / MANAGER
    manager.write_text(
        manager.read_text().replace("return await connection.callTool(toolName, args)", call)
    )
    code, report = _check(root, lock, "current")
    assert code != 0 and report["compatible"] is False


@pytest.mark.parametrize(
    "broken_call",
    (
        "await connection.callTool(toolName, args)",
        "return await connection.callTool(toolName, {})",
    ),
)
def test_secondary_method_cannot_hide_broken_primary_tool_forwarding(
    tmp_path: Path, broken_call: str
) -> None:
    root, lock = _snapshot(tmp_path)
    manager = root / MANAGER
    source = manager.read_text().replace(
        "return await connection.callTool(toolName, args)", broken_call
    )
    source = source.replace(
        "  async testServer()",
        (
            "  async callServerTool(serverId, toolName, args) {\n"
            "    return await connection.callTool(toolName, args, { signal });\n  }\n"
            "  async testServer()"
        ),
    )
    manager.write_text(source)
    code, report = _check(root, lock, "current")
    assert code != 0 and report["compatible"] is False
