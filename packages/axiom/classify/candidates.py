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

# How many times a class's identity terms are counted into its scoring vocabulary. See the note in
# `_profile_for`, which explains why they belong there at all.
#
# Matched to the weight the class name already carries, because they are the same kind of signal:
# the words a supplier writes for what the product IS.
#
# The worry with weighting these up was that sibling classes SHARE identity terms — both valve
# classes declare [valve, valves, vlv] — so amplifying them should amplify the vocabulary two near
# neighbours have in common and make them harder to separate. Measured with
# `scripts/measure_dominance.py --sweep-identity`, that does not happen: both populations rise and
# the ambiguous one rises faster, so the gap widens monotonically.
#
#     weight       1       2       3       4       5       6
#     gap     0.0344  0.0379  0.0410  0.0437  0.0461  0.0482
#
# 3 is therefore chosen on the principle rather than the margin — it is what the class name gets,
# and identity terms are the same kind of claim. Going higher buys a slightly wider band by pushing
# every dominance figure up, which is not the same thing as classifying better.
IDENTITY_TERM_WEIGHT = 3

# How hard a term's weight falls off as more classes share it. See _compute_idf.
#
# This replaces a document count, and the replacement is the fix for a real defect rather than a
# tuning knob. The textbook form is log((N+1)/(df+1)) + 1 with N the number of documents; here
# the "documents" are classes, so N was the live class count — and that made every term's weight
# move whenever *any* class was added, including terms the new class does not contain.
#
# It moved them unevenly, which is what did the damage: shared vocabulary gained more than
# distinctive vocabulary, so adding an unrelated class pulled near neighbours together. On the
# two-class valve schema, adding built-in dishwashers took a df=2 term from log(3/3)+1 = 1.000 to
# log(4/3)+1 = 1.288 (+29%) while a df=1 term went from 1.405 to 1.693 (+20%). The correct
# ball-valve match drifted 0.659 -> 0.696 and broke a threshold it had nothing to do with.
#
# Two candidate fixes were measured with scripts/measure_dominance.py before this one was kept.
#
#   * `max(len(profiles), 50)`, as an earlier note in classifier.py proposed. Rejected: a floor
#     only defers the problem to the fifty-first class, and it arrives exactly when the taxonomy
#     is largest and re-measuring is hardest.
#   * A large fixed N. Rejected on measurement — it *closes the gap the threshold lives in*.
#     Large N flattens the ratio between a term unique to one class and a term shared by two
#     (at N=512, 6.55 vs 6.14, only 1.07x), and that ratio is precisely what separates a ball
#     valve from a gate valve. `--sweep-corpus 4 8 32 128 512 4096` reported the gap shrinking
#     monotonically from +0.0383 to -0.0207, i.e. no threshold separates the populations at all.
#     A small fixed N is worse still: at N=8 a term shared by 30 classes scores
#     log(9/31)+1 = -0.24, and a negative weight actively rewards a class for sharing promiscuous
#     vocabulary.
#
# So the count is removed from the formula entirely and the weight is made a function of document
# frequency alone:
#
#     idf(df) = 1 + log(1 + IDF_SATURATION / df)
#
# which is corpus-independent by construction, strictly decreasing in df, and bounded below by
# 1.0 so a term shared by every class is merely uninformative rather than harmful. Adding a
# decking class now raises df for decking vocabulary and leaves "bronze NPT threaded" exactly
# where it was, so DECISIVE_DOMINANCE stops being corpus-sensitive.
#
# 4.0 sits in the middle of the plateau the sweep found, rather than on its exact argmax:
#
#     K     0.5      1      2      3      4      6      8     16     64
#     gap  .0131  .0293  .0382  .0391  .0382  .0353  .0325  .0248  .0109
#
# K=3 is marginally the widest, but 2..6 are within 0.001 of each other and picking the argmax of
# a flat region is how a constant ends up fitted to two probe strings. For reference the old
# class-count form gave a gap of roughly 0.02 at three classes, so this is about twice the margin
# as well as a stable one.
#
# The shape it gives:
#
#     df=1  -> 1 + log(5.00) = 2.609     unique to one class
#     df=2  -> 1 + log(3.00) = 2.099     1.24x less than unique — the near-neighbour margin
#     df=8  -> 1 + log(1.50) = 1.405
#     df=34 -> 1 + log(1.12) = 1.111     shared by every class in the taxonomy
IDF_SATURATION = 4.0


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping hyphenated and slashed compounds intact.

    ``two-piece`` and ``NPT/BSPT`` are single meaningful tokens in this domain, and splitting
    them loses exactly the distinguishing detail.

    ---------------------------------------------------------------------------------------------
    SPLITTING COMPOUNDS INTO THEIR PARTS WAS TRIED TWICE AND MEASURED WORSE BOTH TIMES. The record
    is here because the idea is a natural one and the argument for it is genuinely persuasive.

    The motivation was real: a separator can hide a word completely. ``DCK225D2 Dewalt Impact/Drill
    - Kit`` yields the single token ``impact/drill`` and matches neither ``impact`` nor ``drill``,
    so an unmistakable power tool reaches no candidate at all. The same artifact hides
    ``sheathing`` inside ``R-Sheathing`` and ``rail`` inside ``T-Rail``.

    Emitting the compound AND its parts looks purely additive — every previous match still works.
    It is not, because ``_profile_for`` tokenises class vocabularies through this same function, so
    the parts land in every class's term counts too:

    * Splitting both separators took coverage from 959 classified rows to 954 and closed the
      dominance gap the decision threshold sits in (an ambiguous probe fell to 0.633, below a
      correct match at 0.730). Hyphen parts are mostly fragments rather than words — ``easi``,
      ``lite``, ``wal``, and every numeric piece of every part number.
    * Splitting only the slash, on the theory that a slash alternates where a hyphen joins, still
      gave 954. Enum values like ``Concrete / Masonry``, ``Indoor / Outdoor`` and ``Smoke / Gray``
      put their parts into many classes at once, so class vocabularies gained shared noise faster
      than queries gained signal, and rows moved out of a clean win into the ambiguous band.

    The conclusion is that a compound spelling is a per-class fact, not a global one, and the schema
    already has the declarative place to say so: ``identity_terms``. ``impact/drill`` is declared on
    the power-tool class, ``r-sheathing`` on the panel class, ``t-rail`` on the railing class. That
    is local, reviewable, and costs nothing anywhere else.
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
        """Inverse document frequency across classes, against a FIXED document count.

        This is what makes hand-maintained stopwords unnecessary: a term appearing in every
        class contributes almost nothing, so "valve" self-suppresses inside a valve taxonomy
        while still discriminating in a broader one.

        There is deliberately no document count in the formula. Using the live class count made
        every score — and therefore every threshold derived from a score — move whenever a class
        was added anywhere in the taxonomy, including for terms the new class does not contain.
        See :data:`IDF_SATURATION` for the measurement behind the replacement.

        Document *frequency* is still counted over the real profiles, which is the part that
        should be local: adding a class that says "decking" raises df for "decking" and touches
        nothing else.
        """
        document_frequency: Counter[str] = Counter()
        for profile in profiles:
            document_frequency.update(set(profile.term_counts))
        return {
            term: 1.0 + math.log(1.0 + IDF_SATURATION / count)
            for term, count in document_frequency.items()
            if count  # a term reaches this table only by appearing in a profile; guard the divide
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

    # Identity terms are part of the SCORING vocabulary as well as the admission guard.
    #
    # They were originally only a guard, and that combination had a silent failure: a class could
    # be admitted on an identity term and then score exactly 0.0, because the term appeared nowhere
    # in the vocabulary it was scored against. `search` drops zero-scoring candidates, so the class
    # vanished after being correctly admitted.
    #
    # It cost three whole cohorts. "25459 Mason Line Brd Orange - 500'" was admitted to the layout
    # class on `mason` and scored 0; so was "T-90043 Deep Medium Organizer" on `organizer`, and
    # "3033-20 Milw M18 24" - Hedge Trimmer" on `trimmer`. All three abstained with
    # `no_viable_candidate` while the class that should have won them sat admitted at zero.
    #
    # The inconsistency was the bug: an identity term is the strongest evidence available that the
    # product IS this class — that is precisely why it is trusted to gate admission — so scoring it
    # at nothing while gating on it cannot be right. It is weighted like the class name for the same
    # reason the class name is weighted up.
    for _ in range(IDENTITY_TERM_WEIGHT):
        for term in definition.identity_terms:
            terms.update(tokenize(term))

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
