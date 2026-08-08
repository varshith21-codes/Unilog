"""Check the console's TypeScript types against the Python models they mirror.

``apps/console/src/lib/types.ts`` is a hand-maintained mirror of the Python domain models. The API
and the fixture exporter share one projection so they cannot drift from each other, but nothing
stopped the TypeScript drifting from both — and the failure is silent. Add an enum member in Python
and the console keeps compiling, keeps type-checking, and quietly cannot represent the new case.

### Why this checks rather than generates

The obvious fix is codegen from the OpenAPI schema, and it does not work here: every endpoint in
``apps/api`` is annotated ``-> dict``, so FastAPI emits ``{"type": "object", "additionalProperties":
true}`` for all of them. Generating from that produces ``Record<string, unknown>``, which is
strictly worse than what is written by hand.

Generating from the Pydantic models directly *would* work mechanically, and is still the wrong
trade. ``types.ts`` carries real documentation — why ``formal_check`` distinguishes null from
clean, why a legacy value is never publishable, what each of L4's four verdict states means — and a
generator would flatten all of it into field declarations. The drift is what needs catching, not the
authorship.

So this asserts the two things that actually break:

*   **Enum members, exactly, both ways.** A Python value the TypeScript cannot express is a case the
    console will mishandle; a TypeScript value Python never emits is dead code that reads as
    supported. Every drift I have seen in this file was of this kind.
*   **Model fields, Python to TypeScript.** A field the API serialises and the console does not
    declare is data the UI cannot reach. The reverse is allowed and reported rather than failed,
    because the projection layer legitimately adds fields — a value arrives carrying ``score``,
    ``decision`` and ``features`` that no Pydantic model holds.

Run it directly, or let CI do it:

    python scripts/check_console_types.py
"""

from __future__ import annotations

import argparse
import enum
import json
import re
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TYPES = REPO_ROOT / "apps" / "console" / "src" / "lib" / "types.ts"

# TypeScript alias -> the Python enum it mirrors, as "module:ClassName".
#
# Written out rather than discovered by name, so that adding a Python enum does not silently start
# or stop being checked. An unmapped enum is a decision, and this is where it is recorded.
ENUM_MAP: dict[str, str] = {
    "DocumentType": "axiom.core.evidence:DocumentType",
    "ValidationLayer": "axiom.core.validation:ValidationLayer",
    "Verdict": "axiom.core.validation:Verdict",
    "Severity": "axiom.core.validation:Severity",
    "DerivationMethod": "axiom.core.values:DerivationMethod",
    "ValueStatus": "axiom.core.values:ValueStatus",
    "GapReason": "axiom.core.gaps:GapReason",
    "RecommendedAction": "axiom.core.gaps:RecommendedAction",
    "ClassificationScheme": "axiom.core.product:ClassificationScheme",
    "LifecycleStatus": "axiom.core.product:LifecycleStatus",
    "Datatype": "axiom.schema.models:Datatype",
    "Requirement": "axiom.schema.models:Requirement",
    "EvidenceRequirement": "axiom.schema.models:EvidenceRequirement",
    "ClaimKind": "axiom.generate.claims:ClaimKind",
    "ClaimVerdict": "axiom.generate.claims:ClaimVerdict",
    "CohortArm": "axiom.evaluation.cohort:Arm",
    "Interchange": "axiom.schema.models:Interchange",
    "SubstitutionRule": "axiom.schema.models:SubstitutionRule",
    # Renamed on the way across: `Verdict` is already taken in types.ts by the validation layers,
    # and two unrelated unions under one name is how a UI ends up rendering a solver verdict with
    # an equivalence label.
    "EquivalenceVerdict": "axiom.resolve.equivalence:Verdict",
    "Compatibility": "axiom.resolve.equivalence:Compatibility",
}

# TypeScript interface -> the Pydantic model it mirrors.
MODEL_MAP: dict[str, str] = {
    "BoundingBox": "axiom.core.evidence:BoundingBox",
    "SourceDocument": "axiom.core.evidence:SourceDocument",
    "EvidenceSpan": "axiom.core.evidence:EvidenceSpan",
    "ValidationResult": "axiom.core.validation:ValidationResult",
    "Quantity": "axiom.core.values:Quantity",
    "ValueRange": "axiom.core.values:ValueRange",
    "AttributeValue": "axiom.core.values:AttributeValue",
    "Gap": "axiom.core.gaps:Gap",
    "Classification": "axiom.core.product:Classification",
    "QualityIndex": "axiom.core.certificate:QualityIndex",
    "CertificateSummary": "axiom.core.certificate:CertificateSummary",
    "EnrichmentCertificate": "axiom.core.certificate:EnrichmentCertificate",
    "AllowedValue": "axiom.schema.models:AllowedValue",
    "CrossFieldRule": "axiom.schema.models:CrossFieldRule",
    "ChannelProfile": "axiom.schema.models:ChannelProfile",
}

# Python fields the console deliberately does not declare, keyed by interface, with the reason.
#
# Empty, and worth keeping empty: right now every serialised field of every mapped model is declared
# in TypeScript. An entry here is a decision somebody made and can defend; a field missing *without*
# an entry is drift. The checker reports an entry that has become unnecessary, so this cannot rot
# into a list of excuses nobody revisits.
ALLOWED_MISSING: dict[str, dict[str, str]] = {}

_ENUM_BLOCK = re.compile(
    r"^export type (?P<name>\w+)\s*=\s*(?P<body>.*?);\s*$", re.MULTILINE | re.DOTALL
)
_INTERFACE_BLOCK = re.compile(
    r"^export interface (?P<name>\w+)(?:\s+extends\s+[\w\s,]+)?\s*\{(?P<body>.*?)^\}",
    re.MULTILINE | re.DOTALL,
)
_STRING_LITERAL = re.compile(r'"([^"]+)"')
# A field declaration: optional `?`, then `:`. Skips comment lines and nested braces.
_FIELD = re.compile(r"^\s{2}(?P<name>[A-Za-z_]\w*)\??\s*:", re.MULTILINE)


@dataclass
class Finding:
    kind: str
    subject: str
    detail: str
    blocking: bool = True


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked_enums: int = 0
    checked_models: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def passed(self) -> bool:
        return not self.blocking


def load(target: str) -> Any:
    module_name, _, attribute = target.partition(":")
    return getattr(import_module(module_name), attribute)


def ts_enums(source: str) -> dict[str, list[str]]:
    """String-literal unions declared in types.ts, in order."""
    found: dict[str, list[str]] = {}
    for match in _ENUM_BLOCK.finditer(source):
        literals = _STRING_LITERAL.findall(match.group("body"))
        if literals:
            found[match.group("name")] = literals
    return found


def ts_interfaces(source: str) -> dict[str, set[str]]:
    """Field names declared on each interface.

    Deliberately shallow: only fields at one level of indentation are read, so a nested object
    literal does not leak its keys into the parent's field set.
    """
    return {
        match.group("name"): set(_FIELD.findall(match.group("body")))
        for match in _INTERFACE_BLOCK.finditer(source)
    }


def check(source: str) -> Report:
    report = Report()
    declared_enums = ts_enums(source)
    declared_interfaces = ts_interfaces(source)

    # ---------------------------------------------------------------- enums
    for ts_name, target in sorted(ENUM_MAP.items()):
        python_enum = load(target)
        if not (isinstance(python_enum, type) and issubclass(python_enum, enum.Enum)):
            report.findings.append(
                Finding("enum", ts_name, f"{target} is not an Enum; the mapping is wrong")
            )
            continue

        expected = [str(member.value) for member in python_enum]
        actual = declared_enums.get(ts_name)

        if actual is None:
            report.findings.append(
                Finding(
                    "enum",
                    ts_name,
                    "declared in ENUM_MAP but not found in types.ts as a string union",
                )
            )
            continue

        report.checked_enums += 1
        missing = [value for value in expected if value not in actual]
        extra = [value for value in actual if value not in expected]

        if missing:
            report.findings.append(
                Finding(
                    "enum",
                    ts_name,
                    f"missing {missing} — Python emits these and the console cannot express them, "
                    f"so any value carrying one will be mishandled. Add them to the union in "
                    f"types.ts (and to any Record<{ts_name}, …> label map).",
                )
            )
        if extra:
            report.findings.append(
                Finding(
                    "enum",
                    ts_name,
                    f"has {extra}, which {target} never emits — dead branches that read as "
                    f"supported cases.",
                )
            )

    # ---------------------------------------------------------------- models
    for ts_name, target in sorted(MODEL_MAP.items()):
        model = load(target)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            report.findings.append(
                Finding("model", ts_name, f"{target} is not a Pydantic model; the mapping is wrong")
            )
            continue

        actual = declared_interfaces.get(ts_name)
        if actual is None:
            report.findings.append(
                Finding("model", ts_name, "declared in MODEL_MAP but not found in types.ts")
            )
            continue

        report.checked_models += 1
        schema = model.model_json_schema(mode="serialization")
        expected = set(schema.get("properties", {}))
        allowed = ALLOWED_MISSING.get(ts_name, {})

        missing = sorted(expected - actual - set(allowed))
        if missing:
            report.findings.append(
                Finding(
                    "model",
                    ts_name,
                    f"missing {missing} — the API serialises these and the console does not "
                    f"declare them, so the data is unreachable. Add them, or record why not in "
                    f"ALLOWED_MISSING.",
                )
            )

        # Projected fields are expected and legitimate, so these are notes rather than failures.
        projected = sorted(actual - expected)
        if projected:
            report.notes.append(
                f"{ts_name}: {len(projected)} field(s) not on the Pydantic model "
                f"({', '.join(projected[:6])}{'…' if len(projected) > 6 else ''}) — "
                f"projection-layer additions"
            )

        stale = sorted(name for name in allowed if name in expected and name in actual)
        if stale:
            report.notes.append(
                f"{ts_name}: ALLOWED_MISSING entries {stale} are now declared in types.ts and can "
                f"be removed from the allowlist"
            )

    unmapped = sorted(set(declared_enums) - set(ENUM_MAP) - {"CanonicalValue"})
    if unmapped:
        report.notes.append(
            "TypeScript unions with no Python enum mapped: "
            + ", ".join(unmapped)
            + " (projection-only vocabularies, or candidates for ENUM_MAP)"
        )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--types", type=Path, default=DEFAULT_TYPES)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--quiet", action="store_true", help="only report drift, not the informational notes"
    )
    args = parser.parse_args()

    if not args.types.is_file():
        print(f"no types file at {args.types}", file=sys.stderr)
        return 2

    report = check(args.types.read_text(encoding="utf-8"))

    if args.json:
        print(
            json.dumps(
                {
                    "passed": report.passed,
                    "checked_enums": report.checked_enums,
                    "checked_models": report.checked_models,
                    "findings": [
                        {"kind": f.kind, "subject": f.subject, "detail": f.detail}
                        for f in report.findings
                    ],
                    "notes": report.notes,
                },
                indent=2,
            )
        )
        return 0 if report.passed else 1

    print("=" * 78)
    print("CONSOLE TYPE DRIFT")
    print("=" * 78)
    print(
        f"  {report.checked_enums} enum(s) and {report.checked_models} model(s) compared against "
        f"their Python definitions"
    )

    if report.blocking:
        print("\n  DRIFT:")
        for finding in report.blocking:
            print(f"\n    [{finding.kind}] {finding.subject}")
            print(f"      {finding.detail}")

    if report.notes and not args.quiet:
        print("\n  NOTES (not failures):")
        for note in report.notes:
            print(f"    {note}")

    print("\n  PASS" if report.passed else "\n  FAIL")
    print()
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
