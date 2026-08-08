"""Entity resolution and attribute-compatibility equivalence.

Blueprint module `resolve`: matching, relationships and equivalence. What is implemented here is
the equivalence half — cross-references based on **normalised specification compatibility** rather
than text similarity, with a verdict that distinguishes a drop-in replacement from a functional
equivalent that needs different fittings.

Blocking and probabilistic record matching are named in the blueprint's layout and are not built.
"""

from axiom.resolve.catalogue import (
    Catalogue,
    CatalogueSource,
    by_class,
    load_catalogue,
    records_from_bundles,
    records_from_golden,
)
from axiom.resolve.equivalence import (
    AttributeComparison,
    Compatibility,
    EquivalenceReport,
    Verdict,
    equivalence,
    rank,
)
from axiom.resolve.report import (
    CrossReference,
    Sweep,
    cross_reference,
    format_cross_reference,
    format_equivalence,
    format_sweep,
    sweep,
)

__all__ = [
    "AttributeComparison",
    "Catalogue",
    "CatalogueSource",
    "Compatibility",
    "CrossReference",
    "EquivalenceReport",
    "Sweep",
    "Verdict",
    "by_class",
    "cross_reference",
    "equivalence",
    "format_cross_reference",
    "format_equivalence",
    "format_sweep",
    "load_catalogue",
    "rank",
    "records_from_bundles",
    "records_from_golden",
    "sweep",
]
