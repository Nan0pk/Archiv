#!/usr/bin/env python3
"""Verify pinned/current CoWork source compatibility without adopting upstream changes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import cast


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"required CoWork source file is missing: {relative}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cowork-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--mode", choices=("pinned", "current"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    root = arguments.cowork_root.resolve()
    lock = cast(dict[str, object], json.loads(arguments.lock.read_text(encoding="utf-8")))
    locked_revision = cast(str, lock["revision"])
    revision = _git(root, "rev-parse", "HEAD")
    package = cast(
        dict[str, object],
        json.loads(_read(root, "package.json")),
    )
    version = cast(str, package["version"])
    engines = cast(dict[str, object], package["engines"])
    node_engine = cast(str, engines["node"])
    checks: list[dict[str, object]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "passed": passed,
                "owner_if_failed": "cowork_integration",
                "detail": detail,
            }
        )

    record("full_git_revision", len(revision) == 40, revision)
    record("node_24_runtime", "24" in node_engine, node_engine)
    if arguments.mode == "pinned":
        record("pinned_revision", revision == locked_revision, revision)
        record("pinned_version", version == lock["version"], version)
        source_blobs = cast(dict[str, str], lock["source_blobs"])
        for relative, expected in source_blobs.items():
            actual = _git(root, "hash-object", relative)
            record(f"pinned_blob:{relative}", actual == expected, actual)

    types_source = _read(root, "src/electron/mcp/types.ts")
    direct_source = _read(root, "src/cli/direct-run.ts")
    transport_source = _read(
        root,
        "src/electron/mcp/client/transports/StdioTransport.ts",
    )
    connection_source = _read(root, "src/electron/mcp/client/MCPServerConnection.ts")
    manager_source = _read(root, "src/electron/mcp/client/MCPClientManager.ts")
    cli_docs = _read(root, "docs/cli.md")

    for field in (
        'export type MCPTransportType = "stdio"',
        "command?: string",
        "args?: string[]",
        "env?: Record<string, string>",
        "cwd?: string",
        "connectionTimeout?: number",
        "requestTimeout?: number",
        "isError?: boolean",
    ):
        record(f"mcp_type:{field}", field in types_source, field)

    for phrase in (
        'case "mcp-add"',
        'case "mcp-test"',
        'transport === "stdio"',
        "command: commandParts[0]",
        "args: commandParts.slice(1)",
        "cwd: path.resolve(args.cwd)",
    ):
        record(f"cowork_cli:{phrase}", phrase in direct_source, phrase)

    for phrase in (
        "...process.env",
        "...env",
        "cwd: cwd || process.cwd()",
        "env: processEnv",
        'stdio: ["pipe", "pipe", "pipe"]',
        "sendRequest(",
    ):
        record(f"stdio_transport:{phrase}", phrase in transport_source, phrase)

    for phrase in (
        'const PROTOCOL_VERSION = "2024-11-05"',
        "MCP_METHODS.INITIALIZE",
        "MCP_METHODS.TOOLS_LIST",
        "MCP_METHODS.TOOLS_CALL",
        "return result as MCPCallResult",
    ):
        record(f"mcp_connection:{phrase}", phrase in connection_source, phrase)

    for phrase in (
        "return await connection.callTool(toolName, args)",
        "async testServer(",
        "tools: toolCount",
    ):
        record(f"mcp_manager:{phrase}", phrase in manager_source, phrase)

    for phrase in (
        "cowork mcp list|add|remove|enable|disable|test",
        "do not require a Control Plane token",
    ):
        record(f"cli_documentation:{phrase}", phrase in cli_docs, phrase)

    compatible = all(cast(bool, item["passed"]) for item in checks)
    report: dict[str, object] = {
        "schema_version": 1,
        "mode": arguments.mode,
        "repository": lock["repository"],
        "locked_revision": locked_revision,
        "cowork_revision": revision,
        "cowork_version": version,
        "node_engine": node_engine,
        "compatible": compatible,
        "upstream_adopted": False,
        "revision_drift": revision != locked_revision,
        "checks": checks,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not compatible:
        failed = [cast(str, item["name"]) for item in checks if not item["passed"]]
        raise SystemExit("CoWork source contract failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
