"""Tests for declarative base and shared mixins."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.models import Base, TimestampMixin


def test_base_is_declarative_base_subclass() -> None:
    """Base is a SQLAlchemy 2.0 DeclarativeBase, not legacy declarative_base()."""
    assert issubclass(Base, DeclarativeBase)


def test_base_metadata_is_accessible() -> None:
    """Base.metadata is a MetaData instance."""
    assert isinstance(Base.metadata, MetaData)


def test_timestamp_mixin_declares_expected_columns() -> None:
    """The mixin annotates created_at and updated_at."""
    assert "created_at" in TimestampMixin.__annotations__
    assert "updated_at" in TimestampMixin.__annotations__
