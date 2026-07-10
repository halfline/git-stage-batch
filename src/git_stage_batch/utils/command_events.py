"""Command streaming event models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class CommandEventRole(Enum):
    """Command event role/type."""

    OUTPUT = "output"
    STDIN_CLOSED = "stdin_closed"
    EXIT = "exit"


@dataclass
class OutputEvent:
    """Represents output from a child file descriptor."""

    role: Literal[CommandEventRole.OUTPUT]
    fd: int
    data: bytes


@dataclass
class StdinClosedEvent:
    """Represents parent closing child stdin."""

    role: Literal[CommandEventRole.STDIN_CLOSED]


@dataclass
class ExitEvent:
    """Represents child process exit."""

    role: Literal[CommandEventRole.EXIT]
    exit_code: int


CommandEvent = OutputEvent | StdinClosedEvent | ExitEvent


@dataclass(frozen=True)
class CapturedFd:
    """Specification for capturing an extra child file descriptor."""

    child_fd: int
