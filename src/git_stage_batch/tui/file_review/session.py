"""Session state for TUI file review."""

from __future__ import annotations

from dataclasses import dataclass

from ..flow import FlowState


@dataclass
class FileReviewSessionState:
    """State for one interactive file review session."""

    flow_state: FlowState
    file_path: str
    page_spec: str | None = None
