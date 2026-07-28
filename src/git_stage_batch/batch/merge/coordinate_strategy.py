"""Resolution values for coordinate-versus-structural merge ambiguity."""

from enum import Enum


AMBIGUITY_KEY = "baseline-coordinate-vs-structural"


class CoordinateStrategyChoice(Enum):
    """A reviewed choice between two valid merge strategies."""

    STRUCTURAL = 1
    RECORDED_COORDINATES = 2
