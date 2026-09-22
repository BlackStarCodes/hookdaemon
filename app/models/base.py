"""SQLAlchemy declarative base and shared mixins.

Models are added in later phases. This module exists so Alembic has a
metadata target to autogenerate against, and so future models inherit a
consistent base with timestamp columns.
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base."""


class TimestampMixin:
    """Adds created_at and updated_at to any model that inherits it.

    Values come from the database (`now()`), so rows written outside the
    ORM still get correct timestamps. `onupdate` refreshes `updated_at` on
    ORM UPDATE statements; direct SQL UPDATEs set it explicitly.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
