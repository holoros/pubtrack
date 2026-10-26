-- pubtrack schema, version 2 (multi author)
-- Snapshots are append only. Every computed value carries the run that produced it,
-- and every run belongs to exactly one tracked author.

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id       TEXT NOT NULL,          -- key in authors.yml
  started         TEXT NOT NULL,          -- ISO 8601 UTC
  finished        TEXT,
  snapshot_date   TEXT NOT NULL,          -- YYYY-MM-DD the counts belong to
  origin          TEXT NOT NULL DEFAULT 'sync',   -- sync | pop_import | v1_import
  scholar_status  TEXT,                   -- ok | failed | skipped | deferred
  openalex_status TEXT,                   -- ok | failed | skipped
  scholar_via     TEXT,                   -- serpapi | scholarly
  serpapi_calls   INTEGER,
  n_scholar       INTEGER,
  n_openalex      INTEGER,
  message         TEXT
);

CREATE TABLE IF NOT EXISTS works (
  work_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id       TEXT NOT NULL,
  gs_id           TEXT,                   -- Scholar author_pub_id (or SerpAPI citation_id)
  openalex_id     TEXT,                   -- e.g. W2741809807
  doi             TEXT,
  title           TEXT NOT NULL,
  title_norm      TEXT NOT NULL,
  year            INTEGER,
  venue           TEXT,
  type            TEXT,                   -- OpenAlex work type (article, dataset, ...); NULL for Scholar only rows
  authors         TEXT,                   -- semicolon separated, from OpenAlex where matched
  n_authors       INTEGER,
  author_position INTEGER,                -- 1 based position of the tracked author
  is_last_author  INTEGER,
  match_method    TEXT,                   -- doi | title_exact | title_fuzzy | override | none
  match_score     REAL,
  excluded        INTEGER NOT NULL DEFAULT 0,
  merged_into     INTEGER REFERENCES works(work_id),
  first_seen      TEXT,
  last_seen       TEXT,
  UNIQUE (author_id, gs_id),
  UNIQUE (author_id, openalex_id)
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
  author_id   TEXT NOT NULL,
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
  kind     TEXT NOT NULL,     -- h_mismatch | ratio | drop | unmatched | override_conflict
  detail   TEXT
);

-- One OpenAlex title search per unmatched Scholar work, so the fallback never repeats itself.
CREATE TABLE IF NOT EXISTS title_lookups (
  author_id   TEXT NOT NULL,
  gs_id       TEXT NOT NULL,
  searched    TEXT NOT NULL,          -- YYYY-MM-DD
  openalex_id TEXT,                   -- linked work, or NULL when nothing passed the bar
  score       REAL,
  PRIMARY KEY (author_id, gs_id)
);

CREATE INDEX IF NOT EXISTS idx_ws_work ON work_snapshots(work_id, source);
CREATE INDEX IF NOT EXISTS idx_works_norm ON works(author_id, title_norm);
CREATE INDEX IF NOT EXISTS idx_flags_run ON flags(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_author ON runs(author_id, snapshot_date);
