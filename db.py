"""
NRS-1 v3 — Database Layer
=========================
SQLite-backed data store with automatic JSONL migration.

Migration path:  JSONL  →  SQLite  →  (future) Supabase
When to migrate to Supabase: multi-user, real-time, or >50K records.

API surface used by app.py:
    db.ensure_ready()          → initialize + migrate on first run
    db.load_df(tickers, days)  → pandas DataFrame, same shape as old load_history()
    db.get_stats()             → summary counts for sidebar
    db.write_run(record)       → called from nrs1_v3.write_history()

Schema: intentionally flat single table at current scale.
Normalise to runs/claims/scores tables when you have complex join queries.
"""

import json
import sqlite3
from pathlib import Path
import pandas as pd

DB_PATH      = Path("nrs1_data.db")
HISTORY_PATH = Path("nrs1_history.jsonl")

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,      -- session_id (or ts fallback for v2 rows)
    ts          TEXT NOT NULL,         -- ISO8601 UTC
    ticker      TEXT,
    claim       TEXT,
    n_score     REAL,
    r_score     REAL,
    m_implied   REAL,
    nr_gap      REAL,
    mr_gap      REAL,
    gap_index   REAL,
    gap_label   TEXT,
    evidence    TEXT,
    ev_ceiling  TEXT,
    mode        TEXT,
    source_tier INTEGER DEFAULT 3,
    source_name TEXT,
    doc_type    TEXT,
    verbatim    TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts     ON runs(ts);
CREATE INDEX IF NOT EXISTS idx_runs_ticker ON runs(ticker);
CREATE INDEX IF NOT EXISTS idx_runs_label  ON runs(gap_label);
"""

_INSERT = """
INSERT OR IGNORE INTO runs
    (id, ts, ticker, claim, n_score, r_score, m_implied,
     nr_gap, mr_gap, gap_index, gap_label, evidence,
     ev_ceiling, mode, source_tier, source_name, doc_type, verbatim)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_UPSERT = """
INSERT OR REPLACE INTO runs
    (id, ts, ticker, claim, n_score, r_score, m_implied,
     nr_gap, mr_gap, gap_index, gap_label, evidence,
     ev_ceiling, mode, source_tier, source_name, doc_type, verbatim)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _row_tuple(r: dict) -> tuple:
    row_id = r.get("session_id") or r.get("ts", "")
    return (
        row_id,
        r.get("ts", ""),
        r.get("ticker"),
        r.get("claim"),
        r.get("n_score"),
        r.get("r_score"),
        r.get("m_implied"),
        r.get("nr_gap"),
        r.get("mr_gap"),
        r.get("gap_index"),
        r.get("gap_label"),
        r.get("evidence"),
        r.get("ev_ceiling"),
        r.get("mode"),
        r.get("source_tier", 3),
        r.get("source_name"),
        r.get("doc_type"),
        r.get("verbatim"),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def initialize() -> None:
    """Create tables and indexes if they don't exist."""
    with _connect() as conn:
        conn.executescript(_DDL)


def migrate_from_jsonl() -> int:
    """
    One-time JSONL → SQLite migration.
    INSERT OR IGNORE means it's safe to call on every startup.
    Returns number of NEW rows inserted this call.
    """
    if not HISTORY_PATH.exists():
        return 0
    inserted = 0
    with _connect() as conn:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    conn.execute(_INSERT, _row_tuple(r))
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    continue
    return inserted


def ensure_ready() -> None:
    """Initialize DB + migrate JSONL. Call once at app startup."""
    initialize()
    migrate_from_jsonl()


def write_run(record: dict) -> None:
    """Write (or replace) a single run. Called from nrs1_v3.write_history()."""
    with _connect() as conn:
        conn.execute(_UPSERT, _row_tuple(record))


def load_df(tickers: list = None,
            days_back: int = None,
            limit: int = 1000) -> pd.DataFrame:
    """
    Return runs as a pandas DataFrame, sorted by ts ascending.
    Falls back to reading JSONL directly if SQLite is unavailable.
    """
    try:
        ensure_ready()
        clauses, params = [], []
        if tickers:
            clauses.append(f"ticker IN ({','.join('?' * len(tickers))})")
            params.extend(tickers)
        if days_back:
            cutoff = (pd.Timestamp.now(tz="UTC")
                      - pd.Timedelta(days=days_back)).isoformat()
            clauses.append("ts >= ?")
            params.append(cutoff)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM runs {where} ORDER BY ts ASC LIMIT {limit}"
        with _connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
    except Exception:
        # Fallback: read directly from JSONL
        df = _load_jsonl()

    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    for col in ("n_score", "r_score", "m_implied", "nr_gap",
                "mr_gap", "gap_index", "source_tier"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_jsonl() -> pd.DataFrame:
    """Direct JSONL reader (fallback only)."""
    if not HISTORY_PATH.exists():
        return pd.DataFrame()
    rows = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_stats() -> dict:
    """Summary counts for the sidebar. Falls back to empty if DB unavailable."""
    try:
        ensure_ready()
        with _connect() as conn:
            total  = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            by_lbl = {r[0]: r[1] for r in conn.execute(
                "SELECT gap_label, COUNT(*) FROM runs GROUP BY gap_label"
            ).fetchall()}
            avg_row = conn.execute("SELECT AVG(gap_index) FROM runs").fetchone()
            avg_gi  = round(float(avg_row[0]), 4) if avg_row[0] else 0.0
            by_tier = {r[0]: r[1] for r in conn.execute(
                "SELECT source_tier, COUNT(*) FROM runs GROUP BY source_tier"
            ).fetchall()}
        return {"total": total, "by_label": by_lbl,
                "avg_gi": avg_gi, "by_tier": by_tier}
    except Exception:
        return {"total": 0, "by_label": {}, "avg_gi": 0.0, "by_tier": {}}
