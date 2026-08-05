"""Loader for the declarative copy policy.

``schema/copy_policy.yaml`` holds editorial and compliance rules — banned phrases, which
attributes substantiate which regulated claims, and what counts as a standards reference. It
lives in YAML for the same reason the validation rules do: the people who should own "never
write 'lifetime guarantee'" are merchandisers and counsel, and a Python list is somewhere they
will never look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "schema" / "copy_policy.yaml"

DEFAULT_TOLERANCE = 0.01


@dataclass(frozen=True)
class RegulatedClaim:
    """A phrase that is legitimate only when a specific attribute substantiates it."""

    phrase: str
    aliases: tuple[str, ...] = ()
    requires_true: tuple[str, ...] = ()
    """Boolean attributes that must be present, verified and True."""

    requires_any: tuple[str, ...] = ()
    """Attributes where presence is enough."""

    note: str | None = None

    @property
    def all_phrases(self) -> tuple[str, ...]:
        # Longest first, so 'lead free' cannot shadow a longer alias containing it.
        return tuple(
            sorted({self.phrase, *self.aliases}, key=len, reverse=True)
        )


@dataclass
class CopyPolicy:
    """Editorial and compliance rules for generated copy."""

    banned_phrases: tuple[str, ...] = ()
    regulated_claims: tuple[RegulatedClaim, ...] = ()
    standards_prefixes: tuple[str, ...] = ()
    designation_patterns: tuple[str, ...] = ()
    quantity_tolerance: float = DEFAULT_TOLERANCE

    _standards_re: re.Pattern[str] | None = field(default=None, repr=False, compare=False)
    _designation_res: tuple[re.Pattern[str], ...] = field(
        default=(), repr=False, compare=False
    )

    @classmethod
    def load(cls, path: Path | str | None = None) -> CopyPolicy:
        target = Path(path) if path else DEFAULT_PATH
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}

        regulated = tuple(
            RegulatedClaim(
                phrase=str(entry["phrase"]).casefold(),
                aliases=tuple(str(a).casefold() for a in entry.get("aliases", ())),
                requires_true=tuple(entry.get("requires_true", ())),
                requires_any=tuple(entry.get("requires_any", ())),
                note=entry.get("note"),
            )
            for entry in payload.get("regulated_claims", ())
        )

        policy = cls(
            banned_phrases=tuple(
                str(p).casefold() for p in payload.get("banned_phrases", ())
            ),
            regulated_claims=regulated,
            standards_prefixes=tuple(payload.get("standards_prefixes", ())),
            designation_patterns=tuple(payload.get("designation_patterns", ())),
            quantity_tolerance=float(payload.get("quantity_tolerance", DEFAULT_TOLERANCE)),
        )
        policy._compile()
        return policy

    def _compile(self) -> None:
        if self.standards_prefixes:
            alternatives = "|".join(
                sorted((re.escape(p) for p in self.standards_prefixes), key=len, reverse=True)
            )
            # A standards reference is the prefix plus whatever designation follows it:
            # "NSF/ANSI 61", "MSS SP-110", "ASME B16.34", or the bare "UL".
            #
            # The designation must be captured, not just the prefix. Matching only "MSS" would
            # let "MSS SP-120" pass on a product certified to SP-110 — a wrong standard number
            # is a fabrication, and checking the issuing body alone would wave it through.
            self._standards_re = re.compile(
                rf"""\b(?:{alternatives})\b        # issuing body
                (?:\s*[/-]\s*[A-Z]+)?             # co-issuer, e.g. /ANSI
                (?:                               # designation
                    \s*[A-Z]{{1,4}}[-\s]?\d[\w.\-]*   # SP-110, B16.34, A126
                    | \s*\d[\w.\-]*                   # 61, 372
                )?""",
                re.IGNORECASE | re.VERBOSE,
            )
        self._designation_res = tuple(
            re.compile(pattern, re.IGNORECASE) for pattern in self.designation_patterns
        )

    def find_standards(self, text: str) -> list[str]:
        """Standards references in a string, de-duplicated, longest match per position."""
        if self._standards_re is None:
            return []
        found = [_trim(match.group(0)) for match in self._standards_re.finditer(text)]
        return list(dict.fromkeys(token for token in found if token))

    def find_designations(self, text: str) -> list[str]:
        found: list[str] = []
        for pattern in self._designation_res:
            found.extend(_trim(match.group(0)) for match in pattern.finditer(text))
        return list(dict.fromkeys(token for token in found if token))

    def summary(self) -> dict[str, object]:
        return {
            "banned_phrases": len(self.banned_phrases),
            "regulated_claims": len(self.regulated_claims),
            "standards_prefixes": len(self.standards_prefixes),
            "designation_patterns": len(self.designation_patterns),
            "quantity_tolerance": self.quantity_tolerance,
        }


def _trim(token: str) -> str:
    """Strip sentence punctuation a designation pattern swallowed.

    ``ASME B16.34`` legitimately contains a dot, so the pattern has to allow one — which means a
    sentence ending in that standard yields ``ASME B16.34.`` with a trailing full stop. Comparing
    that against the record would fail to match the very standard the product holds, rejecting
    honest copy for its punctuation.
    """
    return token.strip().rstrip(".,;:!?)")


def load_policy(path: Path | str | None = None) -> CopyPolicy:
    return CopyPolicy.load(path)
