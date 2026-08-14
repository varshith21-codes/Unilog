"""The MCP server: the enriched catalogue as tools an agent can call.

Blueprint Tier 3, item 21. Built against the official Python SDK's v2 line
(``from mcp.server import MCPServer``, ``@mcp.tool()``, ``mcp.run()``).

### Why this file is thin

Every tool here is a three-line wrapper. All the behaviour — the filtering, the verdicts, and above
all the provenance contract — lives in :mod:`axiom.agents.tools`, which imports nothing from ``mcp``
and is therefore testable, free, and reproducible without the dependency or a transport.

That split is deliberate and it is the same one the rest of this repository uses for anything with
an external edge: the logic is a pure function over data on disk, and the adapter is a shell. A
guarantee enforced inside a decorated handler is a guarantee no test can reach without standing up
a protocol.

### The import is lazy on purpose

``mcp`` is an optional extra. The tool layer is the part that carries the guarantees and the part CI
gates, so a clone with no MCP installed must still be able to import :mod:`axiom.agents`, run its
tests, and use the tools directly from Python. Only :func:`build_server` needs the SDK, so only
:func:`build_server` imports it — and it fails with an instruction rather than a traceback.

### What an agent can and cannot get from here

It can read typed values with units, provenance on every one of them, ranked substitutes with a
directional verdict, and a citation trail for any single value.

It cannot get a value stripped of its provenance, and it cannot match a compliance claim on
unverified data — :mod:`axiom.agents.tools` refuses both. Those refusals are the reason this server
is worth exposing at all: an agent is a relay, and a catalogue that hands it an unqualified boolean
has effectively published that boolean in natural language everywhere the agent speaks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from axiom.agents.tools import (
    Filter,
    ToolError,
    check_substitution,
    explain_value,
    find_substitutes,
    get_product,
    list_classes,
    search_products,
)
from axiom.resolve import Catalogue, load_catalogue
from axiom.schema import load_default
from axiom.schema.registry import SchemaRegistry

SERVER_NAME = "axiom-catalogue"

INSTRUCTIONS = """\
AXIOM exposes a verifiable industrial product catalogue.

Every attribute value you receive carries a `provenance` block and two flags you must respect:

  provenance.verified      the value's citation was matched back to its source document
  usable_for_compliance    false means you must NOT restate this as an established fact

Compliance attributes (lead_free_compliant, potable_water_approved, approvals, rohs_compliant,
prop65_warning_required, country_of_origin) are legal claims. If usable_for_compliance is false,
say the claim could not be verified rather than reporting the value.

`search_products` will not match a compliance attribute on unverified data, so an empty result for
such a query means "none we can stand behind", not "none exist". The
`excluded_for_unverified_compliance` list names the parts that were held back for that reason.

Substitution verdicts are directional: check_substitution(reference, candidate) asks whether the
candidate can replace the reference, which is a different question from the reverse.

Call list_classes first to learn the attribute codes, datatypes and canonical units.
"""


def build_server(
    catalogue: Catalogue | None = None,
    registry: SchemaRegistry | None = None,
    *,
    bundles: Path | None = None,
    prefer_bundles: bool = False,
    name: str = SERVER_NAME,
) -> Any:
    """Create an MCP server exposing the catalogue.

    Returns the SDK's server object, deliberately typed ``Any`` so this module's annotations do not
    require the optional dependency at import time.
    """
    try:
        from mcp.server import MCPServer
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
        raise ModuleNotFoundError(
            "the MCP server needs the optional 'mcp' extra:\n"
            "    pip install -e '.[mcp]'\n"
            "The tool layer in axiom.agents.tools works without it."
        ) from exc

    registry = registry or load_default()
    catalogue = catalogue or load_catalogue(
        registry, bundles=bundles, prefer_bundles=prefer_bundles
    )

    mcp = MCPServer(name, instructions=INSTRUCTIONS)

    @mcp.tool()
    def axiom_list_classes() -> dict[str, Any]:
        """List product classes with their attributes, datatypes and canonical units.

        Call this first. It is the only way to learn valid attribute codes and the units values
        are denominated in.
        """
        return list_classes(registry)

    @mcp.tool()
    def axiom_get_product(sku: str) -> dict[str, Any]:
        """Get one product's publishable attribute values, each with its provenance.

        Check `usable_for_compliance` before restating any compliance claim.
        """
        return _guard(lambda: get_product(sku, catalogue, registry))

    @mcp.tool()
    def axiom_search_products(
        class_code: str | None = None,
        attribute_code: str | None = None,
        operator: str = "eq",
        value: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the catalogue by class and one typed attribute constraint.

        `value` is written the way a datasheet states it — "600 PSI", "3/4\"", "Bronze C84400" —
        and is normalised the same way extracted values are, so you do not need to know the
        canonical unit. Operators: eq, gte, lte, contains.

        A compliance attribute is matched only on verified data, so an empty result means "none we
        can stand behind" rather than "none exist".
        """
        filters = (
            (Filter(attribute_code=attribute_code, operator=operator, value=value),)
            if attribute_code
            else ()
        )
        return _guard(
            lambda: search_products(
                catalogue,
                registry,
                class_code=class_code,
                filters=filters,
                limit=limit,
            )
        )

    @mcp.tool()
    def axiom_find_substitutes(sku: str, limit: int = 5) -> dict[str, Any]:
        """Rank alternatives for a part that is out of stock.

        Verdicts distinguish a drop-in replacement from a functional equivalent that needs
        different fittings, and report which attribute blocks a substitution.
        """
        return _guard(lambda: find_substitutes(sku, catalogue, registry, limit=limit))

    @mcp.tool()
    def axiom_check_substitution(reference_sku: str, candidate_sku: str) -> dict[str, Any]:
        """Ask whether candidate_sku can replace reference_sku.

        Directional. Swapping the arguments asks a different question and often gets a different
        answer, because a higher-rated part substitutes for a lower-rated one and not the reverse.
        """
        return _guard(
            lambda: check_substitution(reference_sku, candidate_sku, catalogue, registry)
        )

    @mcp.tool()
    def axiom_explain_value(sku: str, attribute_code: str) -> dict[str, Any]:
        """Show where one value came from: method, verbatim quotes, pages, validation results.

        Use this to answer "how do you know". If the attribute is not known, the response says so
        explicitly — an unknown value is not zero, false or empty.
        """
        return _guard(lambda: explain_value(sku, attribute_code, catalogue, registry))

    return mcp


def _guard(call) -> dict[str, Any]:
    """Turn a :class:`ToolError` into a structured refusal rather than a stack trace.

    An agent handed an exception string usually retries the same call. An agent handed
    ``{"error": ..., "recoverable": true}`` and a list of valid SKUs can fix its own query, which is
    the difference between a tool that is usable by a model and one that merely works.
    """
    try:
        return call()
    except ToolError as exc:
        return {"error": str(exc), "recoverable": True}
