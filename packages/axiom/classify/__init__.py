"""Multi-target classification: retrieve candidates, then decide.

Never asks a model to pick from the whole taxonomy. Deterministic retrieval produces a
shortlist, a decisive shortlist is accepted without a model call, and only a genuinely close
call is adjudicated.
"""

from axiom.classify.candidates import (
    MIN_TOKEN_LENGTH,
    Candidate,
    CandidateIndex,
    ClassProfile,
    tokenize,
)
from axiom.classify.classifier import (
    AGREEMENT_SHARPENING,
    DECISIVE_DOMINANCE,
    LEVEL_CONFIDENCE_FLOOR,
    MIN_VIABLE_SCORE,
    ClassificationResult,
    Classifier,
)

__all__ = [
    "AGREEMENT_SHARPENING",
    "DECISIVE_DOMINANCE",
    "LEVEL_CONFIDENCE_FLOOR",
    "MIN_TOKEN_LENGTH",
    "MIN_VIABLE_SCORE",
    "Candidate",
    "CandidateIndex",
    "ClassProfile",
    "ClassificationResult",
    "Classifier",
    "tokenize",
]
