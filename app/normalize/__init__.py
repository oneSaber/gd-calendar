"""归一化层：实体对齐、三层判重、冲突仲裁。"""

from app.normalize.entity import (  # noqa: F401
    TITLE_SIMILARITY_THRESHOLD,
    MatchResult,
    choose_existing,
    is_ambiguous_alias,
    match_venue,
    occurrence_fingerprint,
    soft_fingerprint,
    title_similarity,
    venue_fingerprint,
)

__all__ = [
    "TITLE_SIMILARITY_THRESHOLD", "MatchResult", "choose_existing",
    "is_ambiguous_alias", "match_venue", "occurrence_fingerprint",
    "soft_fingerprint", "title_similarity", "venue_fingerprint",
]
