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
