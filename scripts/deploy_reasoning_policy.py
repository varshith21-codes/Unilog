"""Compile schema/reasoning_policy.yaml into a live Bedrock Automated Reasoning policy.

Creates (or updates) the policy, attaches it to a guardrail, and writes the resulting ARNs to
``packages/axiom/config/reasoning.yaml`` — generated, same pattern as ``models.yaml`` and
``prices.yaml``. Nothing else in the codebase hard-codes an ARN.

    $env:AWS_PROFILE = "axiom"
    python scripts/deploy_reasoning_policy.py            # report only
    python scripts/deploy_reasoning_policy.py --write    # create/update and pin the ARNs
    python scripts/deploy_reasoning_policy.py --destroy  # remove both resources

Everything learned about the API the hard way is recorded in the YAML's own header, because it
is not obvious and cost a long probing session: expressions are SMT-LIB S-expressions, every
variable must reference a declared enum type, there are no numeric or boolean sorts, and rule ids
must be exactly twelve uppercase alphanumerics.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages"))

import yaml  # noqa: E402
from axiom.extract import ModelCascade  # noqa: E402

SOURCE = REPO_ROOT / "schema" / "reasoning_policy.yaml"
OUT = REPO_ROOT / "packages" / "axiom" / "config" / "reasoning.yaml"

RULE_ID = re.compile(r"^[A-Z][0-9A-Z]{11}$")
GUARDRAIL_SUFFIX = "-guardrail"

DEFAULT_CONFIDENCE = 0.9
"""Threshold below which an Automated Reasoning finding is not acted on. Deliberately high: a
formal verdict that fires on a shaky reading of the text is worse than none, because the whole
selling point of this layer is that its verdicts are sound."""

GUARDRAIL_PROFILE = "us.guardrail.v1:0"
"""Automated Reasoning checks require a cross-Region guardrail profile, and the service will
refuse to create the guardrail without one. This identifier is not discoverable through
`list_inference_profiles` — guardrail profiles are a separate namespace with no list operation,
so it is pinned here."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--region", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--confidence", type=float, default=DEFAULT_CONFIDENCE)
    parser.add_argument("--write", action="store_true", help="create or update the resources")
    parser.add_argument("--destroy", action="store_true", help="delete policy and guardrail")
    args = parser.parse_args()

    source = yaml.safe_load(args.source.read_text(encoding="utf-8"))
    definition, problems = compile_policy(source)
    _report_source(source, definition, problems)

    if problems:
        print(
            "\nrefusing to deploy an invalid policy. Fix the source and re-run.", file=sys.stderr
        )
        return 2

    region = args.region or ModelCascade.load().region

    if not (args.write or args.destroy):
        print("\n(dry run — pass --write to deploy, --destroy to remove)")
        return 0

    try:
        import boto3
    except ImportError:
        print("boto3 is required", file=sys.stderr)
        return 1

    client = boto3.Session(profile_name=args.profile, region_name=region).client("bedrock")

    if args.destroy:
        return _destroy(client, source["name"], args.out)

    return _deploy(client, source, definition, region, args)


# ------------------------------------------------------------------ compilation


def compile_policy(source: dict) -> tuple[dict, list[str]]:
    """Turn the declarative source into the API's policyDefinition shape.

    Validation happens here rather than at the API, because the service reports every content
    problem as the same opaque "Policy is not valid." A local check that names the offending rule
    is the difference between a fixable error and a guessing game.
    """
    problems: list[str] = []

    declared_types = {entry["name"] for entry in source.get("types", ())}
    types = [
        {
            "name": entry["name"],
            "description": entry.get("description", "").strip(),
            "values": [
                {
                    "value": value["value"],
                    "description": value.get("description", "").strip(),
                }
                for value in entry["values"]
            ],
        }
        for entry in source.get("types", ())
    ]

    variables = []
    variable_names = set()
    for entry in source.get("variables", ()):
        if entry["type"] not in declared_types:
            problems.append(
                f"variable {entry['name']!r} has type {entry['type']!r}, which is not declared. "
                f"There are no built-in types — not even Int or Bool."
            )
        variable_names.add(entry["name"])
        variables.append(
            {
                "name": entry["name"],
                "type": entry["type"],
                "description": entry.get("description", "").strip(),
            }
        )

    known_values = {
        value["value"] for entry in source.get("types", ()) for value in entry["values"]
    }

    rules = []
    for entry in source.get("rules", ()):
        rule_id = entry["id"]
        if not RULE_ID.match(rule_id):
            problems.append(
                f"rule id {rule_id!r} must match [A-Z][0-9A-Z]{{11}} — exactly twelve "
                f"characters, uppercase letters and digits only, no underscores"
            )

        expression = " ".join(entry["expression"].split())
        problems.extend(_check_expression(rule_id, expression, variable_names, known_values))
        rules.append({"id": rule_id, "expression": expression})

    return (
        {
            "version": str(source.get("version", "1")),
            "types": types,
            "variables": variables,
            "rules": rules,
        },
        problems,
    )


_OPERATORS = {"=", "not", "and", "or", "=>", "ite"}
"""Confirmed against the live API. `distinct` and infix forms are rejected."""

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _check_expression(
    rule_id: str, expression: str, variables: set[str], values: set[str]
) -> list[str]:
    problems: list[str] = []

    if expression.count("(") != expression.count(")"):
        problems.append(f"rule {rule_id}: unbalanced parentheses")
    if not expression.startswith("("):
        problems.append(
            f"rule {rule_id}: expressions are SMT-LIB S-expressions and must start with '('. "
            f"Infix forms like 'a == b' are rejected by the service."
        )

    for token in _TOKEN.findall(expression):
        if token in _OPERATORS or token in variables or token in values:
            continue
        problems.append(
            f"rule {rule_id}: {token!r} is neither a declared variable, a declared type value, "
            f"nor a supported operator ({', '.join(sorted(_OPERATORS))})"
        )
    return problems


# ------------------------------------------------------------------ deployment


def _deploy(client, source: dict, definition: dict, region: str, args) -> int:
    name = source["name"]
    description = " ".join(source.get("description", "").split())[:200]

    existing = _find_policy(client, name)
    if existing:
        print(f"\nupdating existing policy {name!r}")
        client.update_automated_reasoning_policy(
            policyArn=existing, policyDefinition=definition, description=description
        )
        policy_arn = existing
    else:
        print(f"\ncreating policy {name!r}")
        try:
            response = client.create_automated_reasoning_policy(
                name=name, description=description, policyDefinition=definition
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {exc}", file=sys.stderr)
            print(
                "  The service reports every content problem as 'Policy is not valid.' Check "
                "the SMT-LIB syntax and that every variable type is declared.",
                file=sys.stderr,
            )
            return 1
        policy_arn = response["policyArn"]
    print(f"  {policy_arn}")

    guardrail_name = f"{name}{GUARDRAIL_SUFFIX}"
    guardrail_id, guardrail_arn, version = _upsert_guardrail(
        client, guardrail_name, policy_arn, args.confidence
    )
    print(f"\nguardrail {guardrail_name!r}")
    print(f"  {guardrail_arn}")
    print(f"  version {version}, confidence threshold {args.confidence}")

    payload = {
        "region": region,
        "note": (
            "GENERATED by scripts/deploy_reasoning_policy.py from schema/reasoning_policy.yaml. "
            "Do not hand-edit; re-run the script."
        ),
        "policy_name": name,
        "policy_arn": policy_arn,
        "guardrail_id": guardrail_id,
        "guardrail_arn": guardrail_arn,
        "guardrail_version": version,
        "confidence_threshold": args.confidence,
        "rules": [rule["id"] for rule in definition["rules"]],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    print(f"\nwrote {args.out.relative_to(REPO_ROOT)}")
    return 0


def _find_policy(client, name: str) -> str | None:
    paginator = client.get_paginator("list_automated_reasoning_policies")
    for page in paginator.paginate():
        for summary in page.get("automatedReasoningPolicySummaries", ()):
            if summary.get("name") == name:
                return summary["policyArn"]
    return None


def _upsert_guardrail(client, name: str, policy_arn: str, confidence: float):
    config = {
        "automatedReasoningPolicyConfig": {
            "policies": [policy_arn],
            "confidenceThreshold": confidence,
        }
    }
    common = {
        "name": name,
        "description": "AXIOM L6 formal verification of product claims",
        "blockedInputMessaging": "This request could not be verified.",
        "blockedOutputsMessaging": (
            "This statement contradicts the product's established facts."
        ),
        "crossRegionConfig": {"guardrailProfileIdentifier": GUARDRAIL_PROFILE},
        **config,
    }

    existing = _find_guardrail(client, name)
    if existing:
        client.update_guardrail(guardrailIdentifier=existing, **common)
        guardrail_id = existing
    else:
        guardrail_id = client.create_guardrail(**common)["guardrailId"]

    detail = client.get_guardrail(guardrailIdentifier=guardrail_id)
    return guardrail_id, detail["guardrailArn"], detail.get("version", "DRAFT")


def _find_guardrail(client, name: str) -> str | None:
    paginator = client.get_paginator("list_guardrails")
    for page in paginator.paginate():
        for summary in page.get("guardrails", ()):
            if summary.get("name") == name:
                return summary["id"]
    return None


def _destroy(client, name: str, out: Path) -> int:
    guardrail = _find_guardrail(client, f"{name}{GUARDRAIL_SUFFIX}")
    if guardrail:
        client.delete_guardrail(guardrailIdentifier=guardrail)
        print(f"deleted guardrail {name}{GUARDRAIL_SUFFIX}")

    policy = _find_policy(client, name)
    if policy:
        client.delete_automated_reasoning_policy(policyArn=policy)
        print(f"deleted policy {name}")

    if out.exists():
        out.unlink()
        print(f"removed {out.relative_to(REPO_ROOT)}")
    return 0


# ------------------------------------------------------------------ reporting


def _report_source(source: dict, definition: dict, problems: list[str]) -> None:
    print("=" * 78)
    print(f"REASONING POLICY — {source['name']}")
    print("=" * 78)
    print(f"  types      {len(definition['types'])}")
    for entry in definition["types"]:
        print(f"    {entry['name']:<18} {len(entry['values'])} values")
    print(f"  variables  {len(definition['variables'])}")
    print(f"  rules      {len(definition['rules'])}")
    for rule, original in zip(definition["rules"], source.get("rules", ()), strict=False):
        print(f"    {rule['id']}  {original.get('description', '')}")

    if problems:
        print(f"\n  {len(problems)} PROBLEM(S):")
        for problem in problems:
            print(f"    - {problem}")
    else:
        print("\n  source is internally consistent")
    print(
        "\n  Note: enum-only. Numeric rules stay in L2, which has real arithmetic — encoding\n"
        "  them as enum bands here would lose precision, and a sound proof over the wrong\n"
        "  model is worse than no proof."
    )


if __name__ == "__main__":
    raise SystemExit(main())
