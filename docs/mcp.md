# Bounded local MCP server

Archiv exposes its verified capabilities to replaceable workbenches through the official MCP Python SDK v2. The first transport is stdio: the host launches `archiv-mcp` as a local child process and communicates over stdin/stdout. Archiv does not open a port.

## Host configuration

Configure the workbench to launch the installed entry point and provide the archive location explicitly:

```json
{
  "mcpServers": {
    "archiv": {
      "command": "archiv-mcp",
      "env": {
        "ARCHIV_HOME": "/absolute/path/to/archiv-home"
      }
    }
  }
}
```

The same command can be used by CoWork-OS or any other MCP host that supports a local stdio server. No host owns Archiv's canonical data, run status, or validators.

## Tools

| Tool | Effect |
|---|---|
| `archiv_ingest` | Reads one absolute, non-symlink local file and delegates to immutable ingestion. |
| `archiv_search` | Searches literal local text and returns revalidated exact citations. |
| `archiv_read_source` | Revalidates a complete citation and returns its exact normalized excerpt. |
| `archiv_generate_docx` | Writes a new cited DOCX under `ARCHIV_HOME/outputs/mcp/` and validates it. |
| `archiv_verify_artifact` | Revalidates one MCP-owned DOCX and its fixed manifest sidecar. |
| `archiv_get_run_evidence` | Reads only `request.json` and `result.json` for one MCP run ID. |

Every input and output contract is versioned with `schema_version: "1"`.

## Policy boundary

The MCP server deliberately does not expose:

- a shell or arbitrary command execution;
- URL fetching or any network client;
- raw SQL or FTS syntax;
- an arbitrary output path;
- directory ingestion;
- symbolic-link ingestion;
- arbitrary file reads;
- deletion or overwrite tools.

`ARCHIV_HOME` is fixed by the server process rather than chosen per call. Ingest requires an absolute regular-file path and defaults to a 100 MiB maximum, configurable through `ARCHIV_MCP_MAX_SOURCE_BYTES`. Report names must be simple `.docx` basenames and are confined to `ARCHIV_HOME/outputs/mcp/`. Existing reports are not overwritten.

## Evidence and failure semantics

Every tool call creates:

```text
ARCHIV_HOME/runs/mcp/<run-id>/request.json
ARCHIV_HOME/runs/mcp/<run-id>/result.json
```

The directory is unique and append-only. A failed operation writes a terminal `failed` record before raising. MCP converts an ordinary tool exception into a tool result with `is_error=true` and no structured success payload. A client therefore cannot reinterpret validator failure as successful structured output.

`archiv_get_run_evidence` also creates its own run record while referencing the requested prior run. It cannot accept a filesystem path.

## Validation

The test suite connects to the server twice:

1. directly in memory through the SDK client, validating tool names, generated JSON schemas, structured output, and tool-error behavior;
2. through a real `archiv-mcp` stdio subprocess, validating the installed entry point and wire transport.

The dedicated `MCP validation` GitHub Actions workflow runs both paths on a public hosted runner, builds a synthetic end-to-end tool sequence, and uploads the run ledger, report, summary, and JUnit evidence. It receives no secrets and uses no self-hosted runner.
