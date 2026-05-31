"""Add unique constraint on reviews(repo, pr_number, reviewed_sha)

Revision ID: 003
Revises: 002
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"


def upgrade():
    op.create_unique_constraint(
        "uq_reviews_repo_pr_sha",
        "reviews",
        ["repo", "pr_number", "reviewed_sha"],
    )


def downgrade():
    op.drop_constraint("uq_reviews_repo_pr_sha", "reviews", type_="unique")
