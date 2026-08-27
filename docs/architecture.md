# Architecture

## Boundary

Archiv is the durable knowledge, capability, provenance, and validation core. Human-facing workbenches such as CoWork-OS are adapters, not owners of Archiv state.

```text
operator surface
      |
      | CLI or MCP
      v
Archiv request boundary
      |
      +-- policy resolution
      +-- direct or model-assisted executor
      +-- task-specific capabilities
      +-- independent validators
      +-- append-only run evidence
      |
      v
canonical originals + structured metadata + rebuildable indexes
```

## Durable layers

## `ARCHIV_HOME` durability and compatibility contract

`ARCHIV_HOME` selects the entire Archiv state root. An explicit CLI `--home` wins,
then `ARCHIV_HOME`, then `$XDG_DATA_HOME/archiv` (or
`~/.local/share/archiv`). Moving a home is supported only through a verified
backup/export and restore; consumers must not infer additional paths or write
directly into this tree.

| Path | Classification | Compatibility and removal contract |
| --- | --- | --- |
| `layout-version` | **Versioned canonical metadata** | Integer layout schema. Archiv rejects a newer value. Preserve and back up. |
| `archiv.sqlite3` | **Versioned canonical metadata and ledger** | SQLite `user_version` identifies its schema. Preserve and back up; open only through the storage API. |
| `originals/sha256/<prefix>/<sha256>` | **Canonical objects** | Immutable source bytes; the filename and database digest must match their SHA-256. Never edit or remove except through a future supported retention operation. |
| `runs/` | **Canonical run ledger/evidence** | Terminal, append-only execution evidence. JSON is schema-versioned where applicable. Preserve and back up. |
| `outputs/` | **Canonical output manifests/artifacts** | User-visible outputs and their verification evidence. Preserve and back up. |
| `config/` | **Canonical configuration** | User policy and local configuration. Preserve and back up; secrets must not be placed in portable exports. |
| `derived/` | **Normalized, reproducible evidence** | Extracted text, OCR, tables, previews, and provenance. It is durable for auditability and included in backups, but may be removed and rebuilt from canonical originals and metadata. |
| `indexes/` | **Rebuildable cache** | Search/FTS state. Excluded from backups; safe to remove while Archiv is stopped and then rebuild. |
| `temporary/`, `.*.tmp` | **Ephemeral/restartable state** | Never canonical or backed up. Stale entries are reported as orphans and are safe to remove while no Archiv process is active. |
| `archiv.sqlite3.pre-migration` | **Recovery point** | Byte-consistent snapshot made before the first schema migration. Retain until the migrated home and a new backup have been verified; then safe to remove. |
| `.<home>.pre-restore` (sibling) | **Recovery point** | Previous populated destination retained by `restore --replace`. Remove only after verifying the restored home and backup. |

Unknown top-level entries are not part of the compatibility contract and are
never silently deleted. Backups contain only the declared durable paths, a
consistent SQLite snapshot, and a versioned manifest with a size and SHA-256 for
every member. Restore validates the complete manifest before accepting state,
relocates recorded paths, rebuilds indexes, and runs database, foreign-key,
canonical-object, and JSON-evidence checks. A populated destination requires
the explicit `--replace` option and retains the pre-restore recovery point.

### Schema upgrades and downgrades

Database schema `0` (the unversioned initial release) and schema `1` are the
supported durable inputs. New homes are created at schema `1`. Upgrade steps are
ordered, idempotent, and run in individual `BEGIN IMMEDIATE` transactions;
`PRAGMA user_version` advances only in the same commit as its step. Thus a crash
leaves either the old version or a complete new version and startup can retry.
Before an existing database changes, Archiv creates the SQLite-consistent
`archiv.sqlite3.pre-migration` recovery point without overwriting an earlier
one.

Archiv never performs an in-place downgrade. If a database or layout version is
newer than the running binary, startup fails with instructions to upgrade Archiv
or restore a backup created by a compatible release. For a version older than
the minimum supported version, install an intermediate Archiv release and
migrate sequentially. Copying tables or changing `user_version` manually is
unsupported and can invalidate hashes, provenance, and recovery guarantees.

### Operational integrity

`doctor --home`, `status`, backup, and restore expose or enforce the same core
checks: SQLite `integrity_check` and foreign keys, database schema compatibility,
canonical-object hashes, parseable normalized evidence/run-ledger/output JSON,
available filesystem bytes, and orphaned temporary state. Backup refuses a
home that fails integrity or cannot fit its durable bytes. Restore reserves
staging space, rejects truncated or undeclared members, verifies every size and
hash before use, and rolls a populated destination back on failure.

### Canonical originals

Original bytes are content-addressed, hashed, and never silently overwritten.

### Normalized representations

Extracted text, OCR, transcripts, tables, previews, and structural metadata are derived products that can be rebuilt.

### Structured metadata

SQLite initially stores file identity, hashes, dates, relationships, processing state, and provenance.

### Search

SQLite FTS5 is the first search engine. Embeddings may be added later as another rebuildable index only after benchmark evidence.

### Capabilities

A capability has a versioned input schema, output schema, allowed reads, allowed writes, forbidden paths, timeout policy, network policy, evidence output, and validator.

### Executor

Direct deterministic operations do not require model planning. Model assistance is introduced only when interpretation or drafting is needed. Planned workflows are reserved for genuinely dependent multi-step tasks.

### Validation

Validators live outside the model call and reject missing artifacts, changed sources, malformed Office files, incorrect calculations, unresolved citations, and false completion claims.

## Initial technology choice

Python 3.12 keeps the first core compact and supports document processing, SQLite, Office generation, validation, CLI, and MCP without introducing a second application runtime.
