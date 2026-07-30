# CoWork-OS integration

Archiv treats CoWork-OS as a replaceable operator workbench over the bounded local MCP server. CoWork may plan, present tools, and display artifacts; Archiv remains authoritative for canonical data, tool execution, run status, citations, report validation, and evidence.

## Locked upstream

The accepted workbench revision is recorded in [`upstream-lock.json`](../integrations/cowork-os/upstream-lock.json):

- repository: `CoWork-OS/CoWork-OS`;
- revision: `8c01f1271722a5bb5b8d68ee76b8c68353a564bd`;
- package version: `0.5.50`;
- Node runtime: 24 or newer;
- MCP protocol requested by the pinned client: `2024-11-05`.

The lock also records Git blob IDs for the source files that define the CLI, stdio transport, MCP connection, manager, types, and logger. Changing the lock is a separate manual review decision. The scheduled current-upstream test never writes or updates it.

## Configure the stdio server

Install Archiv into an environment visible to CoWork and start from the example in [`mcp-server.example.json`](../integrations/cowork-os/mcp-server.example.json). The essential fields are:

```json
{
  "transport": "stdio",
  "command": "archiv-mcp",
  "args": [],
  "env": {
    "ARCHIV_HOME": "/absolute/path/to/archiv-home"
  },
  "cwd": "/absolute/path/to/operator-workspace"
}
```

CoWork's server model supports command, arguments, environment, working directory, connection timeout, and request timeout. The process environment is merged with the per-server environment when the child is spawned.

The desktop UI can store the complete configuration. The local CLI also supports `cowork mcp list|add|remove|enable|disable|test`. When using the CLI's add command on Linux, environment can be embedded in the command without opening a network listener:

```bash
cowork mcp add Archiv \
  --transport stdio \
  --command "env ARCHIV_HOME=/absolute/path/to/archiv-home archiv-mcp" \
  --cwd /absolute/path/to/operator-workspace
```

Then obtain the server ID and test it:

```bash
cowork mcp list --json
cowork mcp test <server-id> --json
```

No Control Plane token is required for these local management commands. Prefer the complete desktop configuration when cross-platform environment handling matters.

## Operator behavior

Use [`SKILL.md`](../integrations/cowork-os/SKILL.md) as the workbench instruction. Its central rule is that completion requires structured Archiv success plus run evidence. CoWork prose, an agent task status, or a created file is not sufficient.

A normal cited-report sequence is:

1. `archiv_search`;
2. `archiv_read_source` for selected citations;
3. `archiv_generate_docx`;
4. `archiv_verify_artifact`;
5. `archiv_get_run_evidence` for generation and verification.

The workbench must not substitute shell, browser, generic filesystem, or native Office tools for that sequence.

## Regression strategy

The `CoWork upstream regression` workflow has two independent jobs:

- **pinned** checks the exact locked revision and every recorded Git blob before running the transport test;
- **current** checks upstream `main` at workflow start and records its exact commit without changing the lock.

Both jobs use CoWork's actual `StdioTransport.ts` through a pinned `tsx` runner. They do not install or build the Electron application. This isolates the boundary that Archiv depends on while avoiding unrelated desktop dependencies.

Each job proves:

- CoWork spawns `archiv-mcp` with environment and working directory;
- MCP initialization and discovery return all six bounded tools;
- search and exact-source reading work through CoWork's transport;
- cited DOCX generation and verification succeed;
- run evidence can be read both through MCP and directly outside CoWork;
- synthetic source hashes remain unchanged;
- the original exact source-marker contract still succeeds;
- invalid ingest input returns `isError` without structured success;
- a tampered DOCX fails verification and cannot become success.

The current-upstream lane is an early-warning test, not an adoption mechanism.

## Fault ownership

The combined regression report assigns every stage to one fault domain:

| Fault domain | Examples |
|---|---|
| Archiv | Wrong citation, changed source, invalid DOCX accepted, missing run evidence |
| MCP transport | Initialize/list/call framing, timeout, or `isError` propagation |
| CoWork integration | Child spawn, environment/cwd, tool retention, raw result handling |
| Model provider | A configured model cannot choose or sequence healthy tools |
| Workbench orchestration | Task loop claims completion without required calls/evidence |

The pinned transport regression deliberately marks model provider and workbench orchestration as `not_exercised`; it proves the deterministic integration boundary independently of model behavior. Later agent-level tests can build on this baseline without obscuring whether a defect is in Archiv or the workbench.
