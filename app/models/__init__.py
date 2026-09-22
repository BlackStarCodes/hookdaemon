"""Model package.

Import every model module here so Alembic's autogenerate sees the full
metadata. Currently exports only the base; models are added in later
phases.
"""

from app.models.base import Base, TimestampMixin

__all__ = ["Base", "TimestampMixin"]
