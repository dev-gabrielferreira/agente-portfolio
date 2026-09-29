"""Persistência em SQLite: projetos, jobs, eventos (linha do tempo), sessões e lições."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'new',          -- new | adopted
    status TEXT NOT NULL DEFAULT 'draft',      -- draft | building | live | down | archived
    repo_url TEXT DEFAULT '',
    staging_tag TEXT DEFAULT '',
    prod_tag TEXT DEFAULT '',
    prev_prod_tag TEXT DEFAULT '',
    min_coverage INTEGER,
    min_mutation INTEGER,
    public INTEGER NOT NULL DEFAULT 1,
    stack TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    figma_url TEXT DEFAULT '',
    figma_file_key TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    type TEXT NOT NULL,                        -- create | change | incident | adopt
    request TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',     -- queued | running | waiting | done | failed | cancelled
    waiting_for TEXT DEFAULT '',               -- answers | spec_approval | design_approval | deploy_approval | human
    fix_round INTEGER NOT NULL DEFAULT 0,
    tests_round INTEGER NOT NULL DEFAULT 0,
    mutation_score REAL,
    base_commit TEXT DEFAULT '',
    questions TEXT DEFAULT '[]',
    answers TEXT DEFAULT '{}',
    spec_summary TEXT DEFAULT '{}',
    eval_report TEXT DEFAULT '{}',
    security_report TEXT DEFAULT '{}',
    design_report TEXT DEFAULT '{}',
    test_report TEXT DEFAULT '{}',
    review_report TEXT DEFAULT '{}',
    message TEXT DEFAULT '',
    cost_usd REAL NOT NULL DEFAULT 0,
    not_before TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,                        -- phase | gate | eval | deploy | human | error | info
    message TEXT NOT NULL,
    data TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    role TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    turns INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    log_path TEXT DEFAULT '',
    error TEXT DEFAULT '',
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'rule',         -- rule | skill | check | hook
    evidence TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',   -- proposed | accepted | rejected
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS infra_changes (
    id INTEGER PRIMARY KEY,
    target TEXT NOT NULL DEFAULT 'caddyfile',
    status TEXT NOT NULL DEFAULT 'proposta',   -- proposta | aplicada | revertida | rejeitada | falhou |
                                               -- desfeita | obsoleta
    motivo TEXT NOT NULL,
    origem TEXT NOT NULL DEFAULT 'jarvis',
    base_hash TEXT NOT NULL,
    before TEXT NOT NULL,
    after TEXT NOT NULL,
    diff TEXT NOT NULL,
    alerts TEXT DEFAULT '[]',
    validation TEXT DEFAULT '',
    result TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
"""

JSON_FIELDS = {
    "questions",
    "answers",
    "spec_summary",
    "eval_report",
    "security_report",
    "design_report",
    "test_report",
    "review_report",
    "data",
    "tags",
    "alerts",
}

# colunas acrescentadas depois da primeira versão: (tabela, coluna, definição)
MIGRATIONS = [
    ("projects", "min_mutation", "INTEGER"),
    ("projects", "tags", "TEXT DEFAULT '[]'"),
    ("projects", "figma_url", "TEXT DEFAULT ''"),
    ("projects", "figma_file_key", "TEXT DEFAULT ''"),
    ("jobs", "tests_round", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "mutation_score", "REAL"),
    ("jobs", "design_report", "TEXT DEFAULT '{}'"),
    ("jobs", "test_report", "TEXT DEFAULT '{}'"),
    ("jobs", "review_report", "TEXT DEFAULT '{}'"),
]


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for k in JSON_FIELDS & out.keys():
        try:
            out[k] = json.loads(out[k] or "null")
        except json.JSONDecodeError:
            pass
    return out


class DB:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        for table, column, definition in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # utilitários --------------------------------------------------------------
    def _exec(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def _one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        return _row(self._exec(sql, params).fetchone())

    def _all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        return [_row(r) for r in self._exec(sql, params).fetchall()]  # type: ignore[misc]

    @staticmethod
    def _encode(values: dict[str, Any]) -> dict[str, Any]:
        return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v) for k, v in values.items()}

    # projetos -----------------------------------------------------------------
    def create_project(
        self, slug: str, name: str, description: str = "", kind: str = "new", repo_url: str = ""
    ) -> dict[str, Any]:
        ts = now()
        cur = self._exec(
            "INSERT INTO projects(slug,name,description,kind,repo_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (slug, name, description, kind, repo_url, ts, ts),
        )
        return self.project(cur.lastrowid)  # type: ignore[return-value]

    def project(self, project_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM projects WHERE id=?", (project_id,))

    def project_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM projects WHERE slug=?", (slug,))

    def projects(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM projects WHERE status!='archived' ORDER BY updated_at DESC")

    def update_project(self, project_id: int, **values: Any) -> None:
        values["updated_at"] = now()
        values = self._encode(values)
        cols = ", ".join(f"{k}=?" for k in values)
        self._exec(f"UPDATE projects SET {cols} WHERE id=?", [*values.values(), project_id])

    # jobs ---------------------------------------------------------------------
    def create_job(self, project_id: int, type_: str, request: str, phase: str) -> dict[str, Any]:
        ts = now()
        cur = self._exec(
            "INSERT INTO jobs(project_id,type,request,phase,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (project_id, type_, request, phase, ts, ts),
        )
        return self.job(cur.lastrowid)  # type: ignore[return-value]

    def job(self, job_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def jobs(self, project_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if project_id is None:
            return self._all("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return self._all("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit))

    def active_jobs(self, project_id: int) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting')",
            (project_id,),
        )

    def update_job(self, job_id: int, **values: Any) -> None:
        values["updated_at"] = now()
        values = self._encode(values)
        cols = ", ".join(f"{k}=?" for k in values)
        self._exec(f"UPDATE jobs SET {cols} WHERE id=?", [*values.values(), job_id])

    def next_runnable_job(self) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM jobs WHERE status='queued' AND (not_before='' OR not_before<=?) ORDER BY id LIMIT 1",
            (now(),),
        )

    def reset_running_jobs(self) -> None:
        """Após reinício do processo, jobs 'running' voltam para a fila na mesma fase."""
        self._exec("UPDATE jobs SET status='queued' WHERE status='running'")

    def add_cost(self, job_id: int, cost: float) -> None:
        self._exec("UPDATE jobs SET cost_usd=cost_usd+? WHERE id=?", (cost, job_id))

    # eventos ------------------------------------------------------------------
    def event(self, job_id: int, kind: str, message: str, data: dict | None = None) -> None:
        self._exec(
            "INSERT INTO events(job_id,ts,kind,message,data) VALUES (?,?,?,?,?)",
            (job_id, now(), kind, message, json.dumps(data or {}, ensure_ascii=False)),
        )

    def events(self, job_id: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,))

    def events_since(self, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """Linha do tempo global (todos os jobs), com o projeto de cada evento: é o feed do Jarvis."""
        return self._all(
            "SELECT e.*, j.type AS job_type, j.status AS job_status, j.waiting_for, j.phase,"
            " p.slug AS project_slug, p.name AS project_name"
            " FROM events e JOIN jobs j ON j.id=e.job_id JOIN projects p ON p.id=j.project_id"
            " WHERE e.id>? ORDER BY e.id LIMIT ?",
            (after_id, limit),
        )

    def last_event_id(self) -> int:
        row = self._one("SELECT COALESCE(MAX(id), 0) AS id FROM events")
        return int((row or {}).get("id") or 0)

    # chave-valor (estado pequeno do orquestrador, ex.: anti-replay do TOTP) ---------
    def kv_get(self, key: str, default: str = "") -> str:
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        return str(row["value"]) if row else default

    def kv_set(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO kv(key,value,updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now()),
        )

    # mudanças de infraestrutura (Caddyfile principal) ----------------------------
    def create_infra_change(self, **values: Any) -> dict[str, Any]:
        values.setdefault("created_at", now())
        enc = self._encode(values)
        cols = ",".join(enc)
        cur = self._exec(f"INSERT INTO infra_changes({cols}) VALUES ({','.join('?' * len(enc))})", list(enc.values()))
        return self.infra_change(int(cur.lastrowid or 0)) or {}

    def infra_change(self, change_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM infra_changes WHERE id=?", (change_id,))

    def infra_changes(self, status: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        if status:
            return self._all("SELECT * FROM infra_changes WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return self._all("SELECT * FROM infra_changes ORDER BY id DESC LIMIT ?", (limit,))

    def update_infra_change(self, change_id: int, **values: Any) -> None:
        allowed = {"status", "result", "decided_at", "alerts"}
        enc = self._encode({k: v for k, v in values.items() if k in allowed})
        if enc:
            sets = ",".join(f"{k}=?" for k in enc)
            self._exec(f"UPDATE infra_changes SET {sets} WHERE id=?", [*enc.values(), change_id])

    # sessões ------------------------------------------------------------------
    def add_session(self, job_id: int, role: str, **values: Any) -> None:
        values.setdefault("started_at", now())
        cols = ["job_id", "role", *values.keys()]
        marks = ",".join("?" * len(cols))
        self._exec(
            f"INSERT INTO sessions({','.join(cols)}) VALUES ({marks})",
            [job_id, role, *values.values()],
        )

    def sessions(self, job_id: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM sessions WHERE job_id=? ORDER BY id", (job_id,))

    # lições -------------------------------------------------------------------
    def add_lesson(self, job_id: int | None, text: str, kind: str, evidence: str) -> None:
        self._exec(
            "INSERT INTO lessons(job_id,text,kind,evidence,created_at) VALUES (?,?,?,?,?)",
            (job_id, text, kind, evidence, now()),
        )

    def lessons(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self._all("SELECT * FROM lessons WHERE status=? ORDER BY id DESC", (status,))
        return self._all("SELECT * FROM lessons ORDER BY id DESC")

    def lesson(self, lesson_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM lessons WHERE id=?", (lesson_id,))

    def set_lesson_status(self, lesson_id: int, status: str, text: str | None = None) -> None:
        if text is None:
            self._exec("UPDATE lessons SET status=? WHERE id=?", (status, lesson_id))
        else:
            self._exec("UPDATE lessons SET status=?, text=? WHERE id=?", (status, text, lesson_id))

    # métricas para o steering loop ---------------------------------------------
    def metrics(self) -> dict[str, Any]:
        q = (
            self._one(
                "SELECT COUNT(*) AS jobs, SUM(status='done') AS done, SUM(status='failed') AS failed,"
                " AVG(fix_round) AS avg_rounds, SUM(cost_usd) AS cost FROM jobs"
            )
            or {}
        )
        gates = (
            self._one(
                "SELECT SUM(kind='gate' AND message LIKE '%falhou%') AS gate_fail,"
                " SUM(kind='eval' AND message LIKE '%NEEDS_WORK%') AS eval_fail,"
                " SUM(kind='deploy' AND message LIKE '%rollback%') AS rollbacks FROM events"
            )
            or {}
        )
        return {**q, **gates}
