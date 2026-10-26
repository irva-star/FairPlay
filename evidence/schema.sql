-- Evidence store (SPEC section 7). One SQLite file holds every observation,
-- hash-chained so a later edit is detectable, plus the graph tables both
-- dashboard views draw from.

CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, label TEXT, created_at TEXT, config_hash TEXT,
  plan_hash TEXT, approval_id INTEGER, git_commit TEXT, status TEXT,
  switch TEXT, app TEXT);

CREATE TABLE IF NOT EXISTS approvals(
  approval_id INTEGER PRIMARY KEY,
  kind TEXT CHECK(kind IN ('run_plan','finding','export')),
  object_hash TEXT, approver TEXT, approved_at TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, run_id TEXT, persona TEXT, seed INTEGER,
  started_at TEXT, ended_at TEXT, status TEXT, attempts INTEGER);

CREATE TABLE IF NOT EXISTS events(
  event_id INTEGER PRIMARY KEY, session_id TEXT, seq INTEGER, ts TEXT,
  type TEXT, level_no INTEGER, payload TEXT, screenshot_id TEXT,
  prev_hash TEXT, row_hash TEXT);

CREATE TABLE IF NOT EXISTS screenshots(
  screenshot_id TEXT PRIMARY KEY, path TEXT, sha256 TEXT, taken_at TEXT);

CREATE TABLE IF NOT EXISTS metrics(
  session_id TEXT, metric TEXT, value REAL, PRIMARY KEY(session_id, metric));

CREATE TABLE IF NOT EXISTS results(
  run_id TEXT, contrast TEXT, metric TEXT, n_a INTEGER, n_b INTEGER,
  diff REAL, ci_low REAL, ci_high REAL, hedges_g REAL, p_raw REAL, p_holm REAL,
  tost_p REAL, margin REAL, verdict TEXT, prereg_hash TEXT, computed_at TEXT);

CREATE TABLE IF NOT EXISTS nodes(
  node_id TEXT PRIMARY KEY, view TEXT, kind TEXT, label TEXT, attrs TEXT);

CREATE TABLE IF NOT EXISTS edges(
  edge_id TEXT PRIMARY KEY, view TEXT, src TEXT, dst TEXT, kind TEXT,
  certainty TEXT, source_url TEXT, claim TEXT, evidence_ref TEXT, reviewed_by TEXT);

-- Read by the budget guard to recover spend across processes.
CREATE TABLE IF NOT EXISTS spend(
  spend_id INTEGER PRIMARY KEY, ts TEXT, run_id TEXT, session_id TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER,
  cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER, usd REAL);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_run ON sessions(run_id);
CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id, contrast, metric);
CREATE INDEX IF NOT EXISTS idx_spend_run ON spend(run_id);
