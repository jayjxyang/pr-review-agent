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
