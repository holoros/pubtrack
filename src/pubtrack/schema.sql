-- pubtrack schema, version 1
-- Snapshots are append only. Every computed value carries the run that produced it.

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started         TEXT NOT NULL,          -- ISO 8601 UTC
  finished        TEXT,
  snapshot_date   TEXT NOT NULL,          -- YYYY-MM-DD the counts belong to
  origin          TEXT NOT NULL DEFAULT 'sync',   -- sync | pop_import
  scholar_status  TEXT,                   -- ok | failed | skipped
  openalex_status TEXT,                   -- ok | failed | skipped
  n_scholar       INTEGER,
  n_openalex      INTEGER,
  message         TEXT
);

CREATE TABLE IF NOT EXISTS works (
  work_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  gs_id           TEXT UNIQUE,            -- Scholar author_pub_id
  openalex_id     TEXT UNIQUE,            -- e.g. W2741809807
  doi             TEXT,
  title           TEXT NOT NULL,
  title_norm      TEXT NOT NULL,
  year            INTEGER,
  venue           TEXT,
  authors         TEXT,                   -- semicolon separated, from OpenAlex where matched
  n_authors       INTEGER,
  author_position INTEGER,                -- 1 based position of the tracked author
  is_last_author  INTEGER,
  match_method    TEXT,                   -- doi | title_exact | title_fuzzy | override | none
  match_score     REAL,
  excluded        INTEGER NOT NULL DEFAULT 0,
  merged_into     INTEGER REFERENCES works(work_id),
  first_seen      TEXT,
  last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS work_snapshots (
  run_id    INTEGER NOT NULL REFERENCES runs(run_id),
  work_id   INTEGER NOT NULL REFERENCES works(work_id),
  source    TEXT NOT NULL CHECK (source IN ('scholar', 'openalex')),
  citations INTEGER NOT NULL,
  PRIMARY KEY (run_id, work_id, source)
);

CREATE TABLE IF NOT EXISTS work_year_counts (
  work_id   INTEGER NOT NULL REFERENCES works(work_id),
  year      INTEGER NOT NULL,
  source    TEXT NOT NULL,
  citations INTEGER NOT NULL,
  run_id    INTEGER NOT NULL REFERENCES runs(run_id),
  PRIMARY KEY (work_id, year, source)
);

CREATE TABLE IF NOT EXISTS profile_snapshots (
  run_id             INTEGER PRIMARY KEY REFERENCES runs(run_id),
  total_citations    INTEGER,
  h_reported         INTEGER,
  i10_reported       INTEGER,
  citations_5y       INTEGER,
  h5_reported        INTEGER,
  i10_5y_reported    INTEGER
);

CREATE TABLE IF NOT EXISTS profile_year_counts (
  run_id    INTEGER NOT NULL REFERENCES runs(run_id),
  year      INTEGER NOT NULL,
  citations INTEGER NOT NULL,
  PRIMARY KEY (run_id, year)
);

CREATE TABLE IF NOT EXISTS work_impact (
  run_id     INTEGER NOT NULL REFERENCES runs(run_id),
  work_id    INTEGER NOT NULL REFERENCES works(work_id),
  fwci       REAL,
  pct_value  REAL,
  top1       INTEGER,
  top10      INTEGER,
  PRIMARY KEY (run_id, work_id)
);

CREATE TABLE IF NOT EXISTS metrics (
  run_id  INTEGER NOT NULL REFERENCES runs(run_id),
  source  TEXT NOT NULL,
  metric  TEXT NOT NULL,
  value   REAL,
  PRIMARY KEY (run_id, source, metric)
);

CREATE TABLE IF NOT EXISTS overrides (
  override_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created     TEXT NOT NULL,
  action      TEXT NOT NULL CHECK (action IN ('merge', 'exclude', 'include', 'set_year', 'set_doi', 'set_openalex')),
  gs_id       TEXT NOT NULL,
  target      TEXT,
  note        TEXT,
  active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS flags (
  run_id   INTEGER NOT NULL REFERENCES runs(run_id),
  work_id  INTEGER,
  kind     TEXT NOT NULL,     -- h_mismatch | ratio | drop | unmatched | no_authors
  detail   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_work ON work_snapshots(work_id, source);
CREATE INDEX IF NOT EXISTS idx_works_norm ON works(title_norm);
CREATE INDEX IF NOT EXISTS idx_flags_run ON flags(run_id);
