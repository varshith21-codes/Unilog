"""Candidate generation for classification.

Classification is retrieval-then-decide, never ask-a-model-to-pick-from-40,000-leaves. The
retrieval step is deterministic and cheap: score every class against the product text using
lexical overlap over a vocabulary built from the class name, its browse path, and the
distinctive values of its bound attributes. Only a shortlist reaches a model.

Two reasons the split matters. It keeps cost proportional to catalogue size rather than to
taxonomy size, and it makes the *hard* part — which of these three similar classes is right —
the only part that needs judgement.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from axiom.schema import ClassDefinition, SchemaRegistry

# Words that carry no discriminating signal in an industrial catalogue. "valve" is not here
# on purpose: within a valve taxonomy it is uninformative, but the vocabulary is built per
# class so IDF handles that automatically rather than by hand-maintained stopwords.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "for", "with", "to", "in", "on", "by",
        "is", "are", "be", "this", "that", "from", "at", "as", "it", "its",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")

MIN_TOKEN_LENGTH = 2


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping hyphenated and slashed compounds intact.

    ``two-piece`` and ``NPT/BSPT`` are single meaningful tokens in this domain, and splitting
    them loses exactly the distinguishing detail.
    """
    return [
        token
        for token in _TOKEN.findall(text.lower())
        if len(token) >= MIN_TOKEN_LENGTH and token not in _STOPWORDS
    ]


@dataclass(frozen=True)
class ClassProfile:
    """A class rendered as a bag of words, for retrieval."""

    code: str
    name: str
    browse_path: tuple[str, ...]
    term_counts: Counter[str]
    total_terms: int
    identity_terms: frozenset[str] = frozenset()
    """Terms that must be present for this class to be considered. See
    :attr:`~axiom.schema.models.ClassDefinition.identity_terms`. Empty means no guard."""

    def admits(self, query_terms: Counter[str]) -> bool:
        """Whether the text contains any evidence that the product IS this kind of thing.

        The scorer cannot answer this, because TF-IDF treats a class's attribute vocabulary and
        its identity vocabulary identically — and attribute vocabulary is promiscuous. "Port
        Type" makes a decor plate look like a ball valve, and it scores higher than a real
        dishwasher does against the dishwasher class. Overlap is simply the wrong instrument for
        an identity question, so identity is asked separately and first.
        """
        if not self.identity_terms:
            return True
        return bool(self.identity_terms & set(query_terms))

    def score(self, query_terms: Counter[str], idf: dict[str, float]) -> float:
        """TF-IDF cosine-ish overlap. Deterministic, explainable, and free."""
        if not self.total_terms:
            return 0.0
        shared = set(query_terms) & set(self.term_counts)
        if not shared:
            return 0.0
        numerator = sum(
            query_terms[term] * self.term_counts[term] * (idf.get(term, 1.0) ** 2)
            for term in shared
        )
        query_norm = math.sqrt(
            sum((count * idf.get(term, 1.0)) ** 2 for term, count in query_terms.items())
        )
        class_norm = math.sqrt(
            sum((count * idf.get(term, 1.0)) ** 2 for term, count in self.term_counts.items())
        )
        if query_norm == 0 or class_norm == 0:
            return 0.0
        return numerator / (query_norm * class_norm)


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    browse_path: tuple[str, ...]
    score: float

    @property
    def path_text(self) -> str:
        return " > ".join(self.browse_path)


class CandidateIndex:
    """Searchable index over the classes a schema defines."""

    def __init__(self, profiles: list[ClassProfile]) -> None:
        self._profiles = profiles
        self._idf = self._compute_idf(profiles)

    @classmethod
    def build(cls, registry: SchemaRegistry) -> CandidateIndex:
        return cls(
            [
                _profile_for(registry, registry.product_class(code))
                for code in registry.class_codes
            ]
        )

    @staticmethod
    def _compute_idf(profiles: list[ClassProfile]) -> dict[str, float]:
        """Inverse document frequency across classes.

        This is what makes hand-maintained stopwords unnecessary: a term appearing in every
        class contributes almost nothing, so "valve" self-suppresses inside a valve taxonomy
        while still discriminating in a broader one.
        """
        total = len(profiles) or 1
        document_frequency: Counter[str] = Counter()
        for profile in profiles:
            document_frequency.update(set(profile.term_counts))
        return {
            term: math.log((total + 1) / (count + 1)) + 1.0
            for term, count in document_frequency.items()
        }

    def __len__(self) -> int:
        return len(self._profiles)

    def search(self, text: str, *, limit: int = 5) -> list[Candidate]:
        """Rank classes against free text. Zero-scoring classes are excluded.

        Classes are filtered by :meth:`ClassProfile.admits` *before* scoring, not after. A class
        the text gives no identity evidence for is not a weak candidate to be out-ranked, it is
        not a candidate — and letting it into the ranking would also distort the dominance ratio
        the decision layer computes from the top two scores.
        """
        query = Counter(tokenize(text))
        if not query:
            return []
        scored = [
            Candidate(
                code=profile.code,
                name=profile.name,
                browse_path=profile.browse_path,
                score=round(profile.score(query, self._idf), 6),
            )
            for profile in self._profiles
            if profile.admits(query)
        ]
        ranked = sorted(
            (c for c in scored if c.score > 0.0), key=lambda c: (-c.score, c.code)
        )
        return ranked[:limit]


def _profile_for(registry: SchemaRegistry, definition: ClassDefinition) -> ClassProfile:
    """Build a class's retrieval vocabulary.

    Attribute *values* are included, not just attribute names. What distinguishes a ball valve
    from a gate valve in a supplier description is rarely the word "ball" — it is the presence
    of "full port", "RPTFE", "two-piece". Those live in the enum values, so that is where the
    discriminating signal is.
    """
    terms: Counter[str] = Counter()

    # Class identity, weighted up: an explicit name match should dominate incidental overlap.
    for _ in range(3):
        terms.update(tokenize(definition.name))
    for segment in definition.browse_path:
        terms.update(tokenize(segment))

    for attribute in registry.attributes_for(definition.code):
        terms.update(tokenize(attribute.name))
        for allowed in attribute.allowed_values:
            terms.update(tokenize(allowed.value))
            for alias in allowed.aliases:
                terms.update(tokenize(alias))

    # Identity terms are tokenized through the same function as the query, so a declared term is
    # matched on the same basis as the text it is compared against.
    identity = frozenset(
        token for term in definition.identity_terms for token in tokenize(term)
    )

    return ClassProfile(
        code=definition.code,
        name=definition.name,
        browse_path=definition.browse_path,
        term_counts=terms,
        total_terms=sum(terms.values()),
        identity_terms=identity,
    )
