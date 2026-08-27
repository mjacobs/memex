"""ULID ids — lowercase, sortable; doc id == feed order (contracts.md)."""

from ulid import ULID


def new_ulid() -> str:
    return str(ULID()).lower()
