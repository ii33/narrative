"""Shared application error type for CLI, bridge and legacy operations."""

from __future__ import annotations


class OperationError(ValueError):
    """An expected application failure an adapter can present to its user."""
