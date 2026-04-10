"""tailtest.core.recommendations -- Recommendation schema and dismissal store.

See Phase 3 Task 3.2 for the task spec.
"""

from tailtest.core.recommendations.schema import (
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)
from tailtest.core.recommendations.store import DismissalStore

__all__ = [
    "Recommendation",
    "RecommendationKind",
    "RecommendationPriority",
    "DismissalStore",
]
