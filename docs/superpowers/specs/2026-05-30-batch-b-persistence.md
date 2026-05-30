# Batch B — PostgreSQL Persistence: Reviews, Comments, Agent Traces

## Goal

Persist review results and agent reasoning traces to PostgreSQL, enabling future re-review (incremental diffs based on `reviewed_sha`), debugging (trace inspection), and analytics (cross-PR patterns).

---

## 1. Data Model

### reviews

One row per review execution.

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| repo | VARCHAR(255) | e.g. `"org/repo-name"` |
| pr_number | INTEGER | |
| risk_level | VARCHAR(20) | `"low"`, `"medium"`, `"high"` |
| summary | TEXT | Review summary paragraph |
| escalated | BOOLEAN | Whether deep_review was triggered |
| model_used | VARCHAR(100) | Scenario name used (scan or reason) |
| reviewed_sha | VARCHAR(40) | PR HEAD SHA at review time — used for re-review incremental diff |
| total_input_tokens | INTEGER | Cumulative input tokens consumed |
| round_count | INTEGER | Number of ReAct loop iterations |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### review_comments

One row per inline comment within a review.

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| review_id | INTEGER FK → reviews.id | ON DELETE CASCADE |
| filename | VARCHAR(500) | |
| line | INTEGER | |
| severity | VARCHAR(20) | `"error"`, `"warning"`, `"suggestion"` |
| comment | TEXT | |
| resolved | BOOLEAN | DEFAULT false — for future re-review use |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### agent_traces

One row per tool call during the ReAct loop. Debug and prompt optimization data.

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| review_id | INTEGER FK → reviews.id | ON DELETE CASCADE |
| round_number | INTEGER | Which ReAct iteration |
| tool_name | VARCHAR(100) | e.g. `"read_file"`, `"get_pr_diff"` |
| tool_params | JSONB | Tool call arguments |
| tool_result_summary | VARCHAR(500) | First 500 chars of tool result |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### Indexes

```sql
CREATE INDEX idx_reviews_repo_pr ON reviews(repo, pr_number);
CREATE INDEX idx_review_comments_review_id ON review_comments(review_id);
CREATE INDEX idx_agent_traces_review_id ON agent_traces(review_id);
```

---

## 2. File Structure

| File | Responsibility |
|---|---|
| `requirements.txt` | **MODIFY** — Add `sqlalchemy`, `alembic`, `psycopg2-binary` |
| `app/core/config.py` | **MODIFY** — Add `database_url` setting |
| `app/core/database.py` | **NEW** — SQLAlchemy engine, SessionLocal factory, Base |
| `app/models/__init__.py` | **NEW** — Package init |
| `app/models/review.py` | **NEW** — ORM models: Review, ReviewComment, AgentTrace |
| `app/services/persistence.py` | **NEW** — `save_review(repo, pr_number, ref, result)` function |
| `app/agent/state.py` | **MODIFY** — Add `traces: list[dict]` field |
| `app/agent/graph.py` | **MODIFY** — Collect trace data in `post_tool_processing` |
| `app/tasks/review.py` | **MODIFY** — Call `save_review()` after graph completes |
| `.env.example` | **MODIFY** — Add `DATABASE_URL` |
| `alembic.ini` | **NEW** — Alembic config |
| `alembic/env.py` | **NEW** — Alembic environment |
| `alembic/versions/001_initial.py` | **NEW** — Initial migration (3 tables + indexes) |

---

## 3. Component Design

### database.py

```python
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
```

### ORM Models (review.py)

Three SQLAlchemy models mapping to the tables above. `Review` has `comments` and `traces` relationships with cascade delete.

### persistence.py

Single function `save_review(repo, pr_number, ref, result)`:
1. Creates a `Review` row from graph result dict
2. Creates `ReviewComment` rows from `result["comments"]`
3. Creates `AgentTrace` rows from `result["traces"]`
4. Commits in one transaction
5. Returns the `review.id`

On error: logs warning, does not raise (review posting to GitHub should not fail because persistence failed).

### Trace Collection

In `post_tool_processing` node: extract tool name + params from the latest AIMessage's `tool_calls`, append to `state["traces"]` as dicts:

```python
{"round_number": state["round_count"], "tool_name": name, "tool_params": params, "tool_result_summary": result[:500]}
```

Traces are collected in state during graph execution, then batch-written to PG after graph completes.

### Integration in review.py

```python
result = graph.invoke({...})
save_review(repo_full_name, pr_number, ref, result)  # persist to PG
post_review(repo_full_name, pr_number, result)        # post to GitHub
```

---

## 4. Migration Strategy

Use Alembic with a single initial migration creating all 3 tables + indexes. `alembic.ini` reads `DATABASE_URL` from env. `alembic/env.py` imports `Base.metadata` from `app.models`.

Run: `alembic upgrade head` to apply.

---

## 5. Testing

Unit test `save_review()` using SQLite in-memory database (SQLAlchemy makes this trivial — just swap the engine URL). Verify:
- Review row created with correct fields
- Comments linked to review
- Traces linked to review
- Empty comments/traces handled gracefully
- Cascade delete works
