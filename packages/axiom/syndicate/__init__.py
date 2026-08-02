"""Syndication: emit validated, channel-conformant output.

Two rules the whole package exists to enforce. Channel requirements are checked *before*
publishing, so a rejection surfaces in our own UI rather than in a marketplace error report a
day later. And only publishable values leave the system — a value in the review queue must
never reach a feed, however complete it looks.
"""

from axiom.syndicate.channels import (
    ChannelReadiness,
    preflight,
    publishable_values,
    render_title,
    truncate_title,
)
from axiom.syndicate.exporters import (
    EXPORTERS,
    Cx1PimExporter,
    Exporter,
    ExportResult,
    SchemaOrgExporter,
    export_all,
)

__all__ = [
    "EXPORTERS",
    "ChannelReadiness",
    "Cx1PimExporter",
    "ExportResult",
    "Exporter",
    "SchemaOrgExporter",
    "export_all",
    "preflight",
    "publishable_values",
    "render_title",
    "truncate_title",
]
