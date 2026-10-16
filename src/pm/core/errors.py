"""Domain errors. Domain code raises these; routes map ``status`` to an HTTP response."""

from __future__ import annotations


class PmError(Exception):
    status = 500

    def __init__(self, msg: str = "", *, status: int | None = None) -> None:
        super().__init__(msg)
        if status is not None:
            self.status = status


class Invalid(PmError):
    status = 400


class Denied(PmError):
    status = 403


class NotFound(PmError):
    status = 404


class Conflict(PmError):
    status = 409
