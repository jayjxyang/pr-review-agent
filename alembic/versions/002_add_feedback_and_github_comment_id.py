"""Add feedback and github_comment_id columns to review_comments.

Revision ID: 002
Revises: 001
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"


def upgrade():
    op.add_column("review_comments", sa.Column("feedback", sa.String(20), nullable=True))
    op.add_column("review_comments", sa.Column("github_comment_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("review_comments", "github_comment_id")
    op.drop_column("review_comments", "feedback")
