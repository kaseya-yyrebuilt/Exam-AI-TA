"""Private on-disk SQLite state and original PDF storage."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
from .schema import QuestionResult


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory: str | Path):
        self.root = Path(directory).resolve()
        self.uploads = self.root / "uploads"
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "exam_grader.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, student_label TEXT NOT NULL,
                    checksum TEXT UNIQUE NOT NULL, pdf_path TEXT NOT NULL,
                    pages INTEGER NOT NULL, uploaded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS results (
                    submission_id TEXT NOT NULL, question_id TEXT NOT NULL,
                    payload TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(submission_id, question_id),
                    FOREIGN KEY(submission_id) REFERENCES submissions(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id TEXT NOT NULL,
                    question_id TEXT NOT NULL, action TEXT NOT NULL,
                    reviewer TEXT NOT NULL, old_value TEXT, new_value TEXT, time TEXT NOT NULL);
            """)

    def connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def import_pdf(self, filename: str, content: bytes) -> tuple[str, bool]:
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("Individual PDFs must be 50 MB or less")
        if not content.startswith(b"%PDF-"):
            raise ValueError("Not a valid PDF file header")
        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Password-protected PDFs aren't supported")
                pages = len(document)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Cannot read PDF: {e}") from e
        if not 1 <= pages <= 200:
            raise ValueError("PDF must contain 1–200 pages")
        checksum = hashlib.sha256(content).hexdigest()
        with self.connect() as conn:
            existing = conn.execute("SELECT id FROM submissions WHERE checksum=?", (checksum,)).fetchone()
            if existing:
                return existing["id"], False
            sid = uuid.uuid4().hex
            path = self.uploads / f"{sid}.pdf"  # user filename never becomes a filesystem path
            path.write_bytes(content)
            label = Path(filename.replace('\\', '/')).name[:150] or "Unnamed student"
            conn.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?)",
                         (sid, label, Path(label).stem, checksum, str(path), pages, now()))
            return sid, True

    def submissions(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM submissions ORDER BY uploaded_at,filename")]

    def submission(self, sid: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        return dict(row)

    def rename(self, sid: str, label: str):
        if not label.strip():
            raise ValueError("Student label cannot be empty")
        with self.connect() as conn:
            conn.execute("UPDATE submissions SET student_label=? WHERE id=?", (label.strip()[:200], sid))

    def get_result(self, sid: str, qid: str) -> QuestionResult | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM results WHERE submission_id=? AND question_id=?", (sid, qid)).fetchone()
        return QuestionResult.model_validate_json(row["payload"]) if row else None

    def results(self, sid: str) -> dict[str, QuestionResult]:
        with self.connect() as conn:
            rows = conn.execute("SELECT question_id,payload FROM results WHERE submission_id=?", (sid,))
            return {row["question_id"]: QuestionResult.model_validate_json(row["payload"]) for row in rows}

    def save_result(self, sid: str, result: QuestionResult, reviewer="", action="save"):
        new = result.model_dump_json()
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM results WHERE submission_id=? AND question_id=?",
                               (sid, result.question_id)).fetchone()
            old = row["payload"] if row else None
            conn.execute("INSERT INTO results (submission_id,question_id,payload,updated_at) VALUES(?,?,?,?) "
                         "ON CONFLICT(submission_id,question_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                         (sid, result.question_id, new, now()))
            conn.execute("INSERT INTO audit(submission_id,question_id,action,reviewer,old_value,new_value,time) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (sid, result.question_id, action, reviewer, old, new, now()))

    def audit_rows(self, sid: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT question_id,action,reviewer,time FROM audit WHERE submission_id=? ORDER BY id DESC", (sid,))]

    def delete_submission(self, sid: str):
        row = self.submission(sid)
        with self.connect() as conn:
            conn.execute("DELETE FROM audit WHERE submission_id=?", (sid,))
            conn.execute("DELETE FROM submissions WHERE id=?", (sid,))
        Path(row["pdf_path"]).unlink(missing_ok=True)

    def save_rubric(self, data: str):
        tmp = self.root / "rubric.json.tmp"
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.root / "rubric.json")

    def read_rubric(self) -> str | None:
        path = self.root / "rubric.json"
        return path.read_text(encoding="utf-8") if path.exists() else None
