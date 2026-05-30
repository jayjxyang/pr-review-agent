# Batch B — PostgreSQL Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist review results and agent traces to PostgreSQL, enabling future re-review, debugging, and analytics.

**Architecture:** SQLAlchemy ORM models map to 3 tables (reviews, review_comments, agent_traces). Trace data is collected in graph state during execution, then batch-written to PG after graph completes. Alembic manages migrations.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic, psycopg2-binary, pytest

---

## File Structure

| File | Responsibility |
|---|---|
| `requirements.txt` | **MODIFY** — Add `sqlalchemy`, `alembic`, `psycopg2-binary` |
| `app/core/config.py` | **MODIFY** — Add `database_url` setting |
| `.env.example` | **MODIFY** — Add `DATABASE_URL` |
| `app/core/database.py` | **NEW** — SQLAlchemy engine, SessionLocal, Base |
| `app/models/__init__.py` | **NEW** — Package init, re-export models |
| `app/models/review.py` | **NEW** — ORM models: Review, ReviewComment, AgentTrace |
| `app/services/persistence.py` | **NEW** — `save_review()` function |
| `app/agent/state.py` | **MODIFY** — Add `traces: list[dict]` field |
| `app/agent/graph.py` | **MODIFY** — Collect traces in `post_tool_processing` |
| `app/tasks/review.py` | **MODIFY** — Add `traces` to initial state, call `save_review()` |
| `alembic.ini` | **NEW** — Alembic config |
| `alembic/env.py` | **NEW** — Alembic migration environment |
| `alembic/script.py.mako` | **NEW** — Alembic migration template |
| `alembic/versions/001_initial_schema.py` | **NEW** — Initial migration |
| `tests/test_persistence.py` | **NEW** — Tests for save_review |

---

### Task 1: Dependencies and Config

**Files:**
- Modify: `requirements.txt`
- Modify: `app/core/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Update requirements.txt**

Add these 3 lines after `structlog==24.1.0`:

```
sqlalchemy==2.0.30
alembic==1.13.1
psycopg2-binary==2.9.9
```

- [ ] **Step 2: Update config.py**

Add `database_url` to the `Settings` class, after `redis_url`:

```python
    database_url: str = "postgresql://localhost:5432/pr_review"
```

- [ ] **Step 3: Update .env.example**

Add after the `REDIS_URL` line:

```
DATABASE_URL=postgresql://user:password@localhost:5432/pr_review
```

- [ ] **Step 4: Install deps**

Run: `pip install sqlalchemy==2.0.30 alembic==1.13.1 psycopg2-binary==2.9.9`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/core/config.py .env.example
git commit -m "chore: add SQLAlchemy, Alembic, psycopg2 deps and database_url config"
```

---

### Task 2: Database Module and ORM Models

**Files:**
- Create: `app/core/database.py`
- Create: `app/models/__init__.py`
- Create: `app/models/review.py`

- [ ] **Step 1: Create database.py**

```python
"""SQLAlchemy engine and session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine():
    return create_engine(get_settings().database_url)


SessionLocal = sessionmaker(bind=_engine())
```

- [ ] **Step 2: Create ORM models**

Create `app/models/review.py`:

```python
"""ORM models for review persistence."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    repo = Column(String(255), nullable=False)
    pr_number = Column(Integer, nullable=False)
    risk_level = Column(String(20), nullable=False, default="low")
    summary = Column(Text, default="")
    escalated = Column(Boolean, default=False)
    model_used = Column(String(100), default="")
    reviewed_sha = Column(String(40), nullable=False)
    total_input_tokens = Column(Integer, default=0)
    round_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    comments = relationship("ReviewComment", back_populates="review", cascade="all, delete-orphan")
    traces = relationship("AgentTrace", back_populates="review", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_reviews_repo_pr", "repo", "pr_number"),
    )


class ReviewComment(Base):
    __tablename__ = "review_comments"

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(500), nullable=False)
    line = Column(Integer, nullable=False)
    severity = Column(String(20), nullable=False, default="suggestion")
    comment = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    review = relationship("Review", back_populates="comments")

    __table_args__ = (
        Index("idx_review_comments_review_id", "review_id"),
    )


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False)
    round_number = Column(Integer, nullable=False)
    tool_name = Column(String(100), nullable=False)
    tool_params = Column(JSONB, default=dict)
    tool_result_summary = Column(String(500), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    review = relationship("Review", back_populates="traces")

    __table_args__ = (
        Index("idx_agent_traces_review_id", "review_id"),
    )
```

- [ ] **Step 3: Create models __init__.py**

```python
"""ORM models package."""

from app.models.review import Review, ReviewComment, AgentTrace

__all__ = ["Review", "ReviewComment", "AgentTrace"]
```

- [ ] **Step 4: Verify imports**

Run: `python -c "from app.models import Review, ReviewComment, AgentTrace; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add app/core/database.py app/models/
git commit -m "feat: add SQLAlchemy database module and ORM models (Review, ReviewComment, AgentTrace)"
```

---

### Task 3: Persistence Service

**Files:**
- Create: `app/services/persistence.py`
- Create: `tests/test_persistence.py`

- [ ] **Step 1: Create persistence.py**

```python
"""Persist review results and agent traces to PostgreSQL."""

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.review import Review, ReviewComment, AgentTrace

logger = get_logger(__name__)


def save_review(repo: str, pr_number: int, ref: str, result: dict) -> int | None:
    """Save review result and traces to PostgreSQL.

    Args:
        repo: Repository full name (e.g. "org/repo").
        pr_number: PR number.
        ref: Reviewed commit SHA.
        result: Graph output dict with keys: risk_level, summary, comments,
                escalated, round_count, total_input_tokens, traces.

    Returns:
        The review ID, or None if persistence failed.
    """
    try:
        session = SessionLocal()
        try:
            review = Review(
                repo=repo,
                pr_number=pr_number,
                risk_level=result.get("risk_level", "low"),
                summary=result.get("summary", ""),
                escalated=result.get("escalated", False),
                model_used=result.get("escalated", False) and "reason" or "scan",
                reviewed_sha=ref,
                total_input_tokens=result.get("total_input_tokens", 0),
                round_count=result.get("round_count", 0),
            )
            session.add(review)
            session.flush()  # Get review.id

            for c in result.get("comments", []):
                session.add(ReviewComment(
                    review_id=review.id,
                    filename=c.get("filename", "unknown"),
                    line=c.get("line", 0),
                    severity=c.get("severity", "suggestion"),
                    comment=c.get("comment", ""),
                ))

            for t in result.get("traces", []):
                session.add(AgentTrace(
                    review_id=review.id,
                    round_number=t.get("round_number", 0),
                    tool_name=t.get("tool_name", ""),
                    tool_params=t.get("tool_params", {}),
                    tool_result_summary=t.get("tool_result_summary", "")[:500],
                ))

            session.commit()
            logger.info("review_persisted", review_id=review.id, repo=repo, pr=pr_number)
            return review.id

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as exc:
        logger.warning("review_persistence_failed", error=str(exc), repo=repo, pr=pr_number)
        return None
```

- [ ] **Step 2: Create tests**

Create `tests/test_persistence.py`:

```python
"""Tests for review persistence using SQLite in-memory database."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.review import Review, ReviewComment, AgentTrace
from app.services.persistence import save_review


def _setup_test_db(monkeypatch):
    """Create an in-memory SQLite database and patch SessionLocal."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.services.persistence.SessionLocal", test_session_factory)
    return test_session_factory


class TestSaveReview:
    def test_saves_review_with_comments_and_traces(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        result = {
            "risk_level": "medium",
            "summary": "Found issues",
            "comments": [
                {"filename": "a.py", "line": 10, "severity": "warning", "comment": "bad pattern"},
                {"filename": "b.py", "line": 20, "severity": "error", "comment": "security issue"},
            ],
            "traces": [
                {"round_number": 1, "tool_name": "get_pr_changed_files", "tool_params": {"repo": "x", "pr_number": 1}, "tool_result_summary": "3 files"},
                {"round_number": 2, "tool_name": "read_file", "tool_params": {"repo": "x", "path": "a.py", "ref": "abc"}, "tool_result_summary": "file content"},
            ],
            "escalated": False,
            "round_count": 5,
            "total_input_tokens": 30000,
        }

        review_id = save_review("org/repo", 42, "abc123def", result)
        assert review_id is not None

        session = session_factory()
        review = session.get(Review, review_id)
        assert review.repo == "org/repo"
        assert review.pr_number == 42
        assert review.risk_level == "medium"
        assert review.reviewed_sha == "abc123def"
        assert review.round_count == 5
        assert review.total_input_tokens == 30000
        assert len(review.comments) == 2
        assert len(review.traces) == 2
        assert review.comments[0].filename == "a.py"
        assert review.traces[0].tool_name == "get_pr_changed_files"
        session.close()

    def test_saves_review_with_empty_comments_and_traces(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        result = {
            "risk_level": "low",
            "summary": "All good",
            "comments": [],
            "traces": [],
            "escalated": False,
            "round_count": 3,
            "total_input_tokens": 10000,
        }

        review_id = save_review("org/repo", 10, "def456", result)
        assert review_id is not None

        session = session_factory()
        review = session.get(Review, review_id)
        assert review.comments == []
        assert review.traces == []
        session.close()

    def test_cascade_delete(self, monkeypatch):
        session_factory = _setup_test_db(monkeypatch)
        result = {
            "risk_level": "high",
            "summary": "Critical",
            "comments": [{"filename": "x.py", "line": 1, "severity": "error", "comment": "bad"}],
            "traces": [{"round_number": 1, "tool_name": "read_file", "tool_params": {}, "tool_result_summary": "ok"}],
            "escalated": True,
            "round_count": 2,
            "total_input_tokens": 5000,
        }

        review_id = save_review("org/repo", 5, "aaa111", result)

        session = session_factory()
        review = session.get(Review, review_id)
        session.delete(review)
        session.commit()

        assert session.query(ReviewComment).filter_by(review_id=review_id).count() == 0
        assert session.query(AgentTrace).filter_by(review_id=review_id).count() == 0
        session.close()

    def test_returns_none_on_failure(self, monkeypatch):
        """If the database is unreachable, save_review returns None instead of raising."""
        monkeypatch.setattr("app.services.persistence.SessionLocal", lambda: (_ for _ in ()).throw(Exception("connection refused")))
        review_id = save_review("org/repo", 1, "xxx", {"risk_level": "low", "summary": "", "comments": [], "traces": []})
        assert review_id is None
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_persistence.py -v`
Expected: 4 tests PASS

- [ ] **Step 4: Commit**

```bash
git add app/services/persistence.py tests/test_persistence.py
git commit -m "feat: add persistence service with save_review() and tests"
```

---

### Task 4: Trace Collection in Graph

**Files:**
- Modify: `app/agent/state.py`
- Modify: `app/agent/graph.py`

- [ ] **Step 1: Add `traces` field to ReviewState**

In `app/agent/state.py`, add after the `tool_call_history` line:

```python
    # Agent traces (for persistence)
    traces: list[dict]
```

- [ ] **Step 2: Update `post_tool_processing` to collect traces**

In `app/agent/graph.py`, replace the `post_tool_processing` function with:

```python
def post_tool_processing(state: ReviewState) -> dict:
    """Record tool call fingerprints for dead loop detection and collect traces."""
    history = list(state.get("tool_call_history", []))
    traces = list(state.get("traces", []))

    # Find the last AIMessage with tool_calls (the one that triggered scan_tools)
    for msg in reversed(state["messages"]):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                params = tc.get("args", {})
                params_str = json.dumps(params, sort_keys=True)
                fingerprint = f"{tc['name']}:{hashlib.md5(params_str.encode()).hexdigest()[:8]}"
                history.append(fingerprint)

                # Find matching ToolMessage result for this tool call
                result_summary = ""
                for tmsg in state["messages"]:
                    if isinstance(tmsg, ToolMessage) and tmsg.tool_call_id == tc.get("id"):
                        result_summary = (tmsg.content or "")[:500]
                        break

                traces.append({
                    "round_number": state["round_count"],
                    "tool_name": tc["name"],
                    "tool_params": params,
                    "tool_result_summary": result_summary,
                })
            break

    return {"tool_call_history": history, "traces": traces}
```

- [ ] **Step 3: Verify existing tests still pass**

Run: `pytest tests/test_agent_graph.py -v`
Expected: all 24 tests PASS

- [ ] **Step 4: Commit**

```bash
git add app/agent/state.py app/agent/graph.py
git commit -m "feat: collect agent traces in post_tool_processing node"
```

---

### Task 5: Wire Persistence into Celery Task

**Files:**
- Modify: `app/tasks/review.py`

- [ ] **Step 1: Update review.py**

Replace the full contents of `app/tasks/review.py` with:

```python
"""Celery task — orchestrates the full review pipeline using the LangGraph agent."""

from celery import Task

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.agent import build_review_graph
from app.services.reviewer import post_review
from app.services.github import get_pr_head_sha
from app.services.persistence import save_review

logger = get_logger(__name__)


@celery_app.task(
    name="tasks.run_review",
    bind=True,
    ignore_result=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def run_review(self: Task, repo_full_name: str, pr_number: int):
    """
    End-to-end PR review using LangGraph agent:
    1. Build graph and invoke with PR context
    2. Graph handles: scan → risk assessment → optional escalation
    3. Persist results to PostgreSQL
    4. Post review to GitHub
    """
    log = logger.bind(repo=repo_full_name, pr=pr_number, task_id=self.request.id)
    log.info("review_started", attempt=self.request.retries + 1)

    try:
        ref = get_pr_head_sha(repo_full_name, pr_number)
        log.info("pr_ref_resolved", ref=ref)

        # Build and invoke graph
        graph = build_review_graph()
        result = graph.invoke({
            "messages": [],
            "repo": repo_full_name,
            "pr_number": pr_number,
            "ref": ref,
            "risk_level": "",
            "summary": "",
            "comments": [],
            "escalated": False,
            "escalate_reason": "",
            "round_count": 0,
            "total_input_tokens": 0,
            "tool_call_history": [],
            "traces": [],
        })

        log.info(
            "agent_complete",
            risk=result["risk_level"],
            escalated=result["escalated"],
            comments=len(result["comments"]),
            traces=len(result.get("traces", [])),
        )

        # Persist to PostgreSQL (non-blocking — failure here doesn't stop GitHub posting)
        save_review(repo_full_name, pr_number, ref, result)

        # Post review to GitHub
        post_review(repo_full_name, pr_number, result)
        log.info("review_posted")

    except Exception as exc:
        log.error("review_failed", error=str(exc), attempt=self.request.retries + 1)
        raise self.retry(exc=exc)

    log.info("review_completed")
```

- [ ] **Step 2: Verify all tests pass**

Run: `pytest tests/ -v`
Expected: all tests PASS (24 graph + 4 persistence = 28)

- [ ] **Step 3: Commit**

```bash
git add app/tasks/review.py
git commit -m "feat: wire save_review() into Celery task pipeline"
```

---

### Task 6: Alembic Migration Setup

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/001_initial_schema.py`

- [ ] **Step 1: Create alembic.ini**

```ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql://localhost:5432/pr_review

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Create alembic/env.py**

```python
"""Alembic migration environment."""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from app.core.database import Base
from app.models import Review, ReviewComment, AgentTrace  # noqa: F401 — ensure models are registered

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment variable if present
db_url = os.environ.get("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create alembic/script.py.mako**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create initial migration**

Create `alembic/versions/001_initial_schema.py`:

```python
"""Initial schema: reviews, review_comments, agent_traces

Revision ID: 001
Revises: None
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo", sa.String(255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="low"),
        sa.Column("summary", sa.Text(), server_default=""),
        sa.Column("escalated", sa.Boolean(), server_default="false"),
        sa.Column("model_used", sa.String(100), server_default=""),
        sa.Column("reviewed_sha", sa.String(40), nullable=False),
        sa.Column("total_input_tokens", sa.Integer(), server_default="0"),
        sa.Column("round_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_reviews_repo_pr", "reviews", ["repo", "pr_number"])

    op.create_table(
        "review_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="suggestion"),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_review_comments_review_id", "review_comments", ["review_id"])

    op.create_table(
        "agent_traces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("tool_params", postgresql.JSONB(), server_default="{}"),
        sa.Column("tool_result_summary", sa.String(500), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_agent_traces_review_id", "agent_traces", ["review_id"])


def downgrade() -> None:
    op.drop_table("agent_traces")
    op.drop_table("review_comments")
    op.drop_table("reviews")
```

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: add Alembic migration setup with initial schema (3 tables)"
```

---

### Task 7: Final Verification

- [ ] **Step 1: Full import chain**

Run: `python -c "from app.agent import build_review_graph; from app.services.persistence import save_review; from app.models import Review, ReviewComment, AgentTrace; print('ok')"`
Expected: `ok`

- [ ] **Step 2: Full test suite**

Run: `pytest tests/ -v`
Expected: all tests PASS (24 graph + 4 persistence = 28)

- [ ] **Step 3: App starts**

Run: `python -c "import uvicorn, threading, time, urllib.request; t = threading.Thread(target=lambda: uvicorn.run('app.main:app', host='127.0.0.1', port=8006, log_level='error'), daemon=True); t.start(); time.sleep(4); print(urllib.request.urlopen('http://127.0.0.1:8006/health').read().decode())"`
Expected: `{"status":"ok"}`
