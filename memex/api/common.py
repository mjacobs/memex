"""Shared API plumbing: contract error shape + entity serialization."""

from pydantic import BaseModel


class ApiError(Exception):
    """Raised by handlers; rendered as {"error": {"code", "message"}}."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def dump(entity: BaseModel, *, include_trace: bool = True) -> dict:
    """Entity JSON per contracts: ISO timestamps; trace only on detail views."""
    exclude = None if include_trace else {"trace"}
    return entity.model_dump(mode="json", exclude=exclude)
