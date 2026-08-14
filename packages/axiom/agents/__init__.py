"""Agent-facing surface: the enriched catalogue as callable, provenance-carrying tools.

Blueprint module ``agents`` and Tier 3 item 21. What is implemented is the **server** half — the
typed tool surface an agent calls, plus an MCP adapter over it.

The one invariant: no tool returns a value without its provenance, and no compliance claim is
matched on unverified data. An agent restates whatever it receives in confident prose, so a
distinction dropped at this boundary is a distinction its user can never recover.

The supervisor/specialist topology and the tool-calling loop the blueprint also names under this
module are **not built**. They would put a model inside a package whose entire value is that it is a
deterministic, auditable surface over records on disk.

:mod:`axiom.agents.tools` has no MCP dependency. :func:`axiom.agents.server.build_server` imports
the SDK lazily, so this package is importable and fully testable without the optional extra.
"""

from axiom.agents.tools import (
    Filter,
    ToolError,
    ValueView,
    check_substitution,
    explain_value,
    find_substitutes,
    get_product,
    list_classes,
    search_products,
    value_view,
)

__all__ = [
    "Filter",
    "ToolError",
    "ValueView",
    "check_substitution",
    "explain_value",
    "find_substitutes",
    "get_product",
    "list_classes",
    "search_products",
    "value_view",
]
