-- A real historical version-1 database: objects/ingestions/processing_runs only,
-- no ingestion_failures (added by the 1->2 migration). Used to prove that a
-- database created before that migration existed upgrades cleanly.
PRAGMA user_version = 1;
CREATE TABLE IF NOT EXISTS objects (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64), size INTEGER NOT NULL CHECK(size >= 0),
    media_type TEXT NOT NULL, source_extension TEXT NOT NULL,
    storage_path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestions (
    ingestion_id TEXT PRIMARY KEY, object_sha256 TEXT NOT NULL REFERENCES objects(sha256),
    source_path TEXT NOT NULL, source_name TEXT NOT NULL, imported_at TEXT NOT NULL,
    duplicate INTEGER NOT NULL CHECK(duplicate IN (0, 1)),
    status TEXT NOT NULL CHECK(status IN ('pending', 'succeeded', 'failed')), error TEXT
);
CREATE TABLE IF NOT EXISTS processing_runs (
    processing_id TEXT PRIMARY KEY, object_sha256 TEXT NOT NULL REFERENCES objects(sha256),
    processor TEXT NOT NULL, processor_version TEXT NOT NULL, parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed', 'skipped')),
    output_kind TEXT NOT NULL, output_path TEXT, output_sha256 TEXT, error TEXT,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingestions_object_idx ON ingestions(object_sha256);
CREATE INDEX IF NOT EXISTS processing_object_idx ON processing_runs(object_sha256);
