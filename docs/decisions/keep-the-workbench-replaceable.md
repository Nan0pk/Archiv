# Keep the workbench replaceable

## Decision

Archiv will expose a CLI and bounded MCP server before adopting or forking a desktop workbench.

## Reason

CoWork-OS is currently the strongest integrated operator surface, but importing its complete application would couple Archiv to a large and rapidly changing product before the durable core and execution contract are proven.

## Consequences

- Archiv owns canonical data, run evidence, policies, and validators;
- CoWork-OS is the first integration target rather than the base repository;
- AionUi remains an Office-oriented comparison surface;
- a fork is considered only if extension boundaries prove insufficient through tests.
