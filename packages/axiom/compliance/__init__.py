"""Regulatory readiness: how far a catalogue is from a Digital Product Passport.

Blueprint Tier 3, item 22, driven by Part 3.3's timing argument — the ESPR central registry was
scheduled for July 2026 and product groups phase in through 2030.

Separate from :mod:`axiom.syndicate` on purpose. That package gets data into commerce systems, and
a channel that rejects a record is a integration problem you fix by fixing the record. A regulation
is not a channel: it has a statutory deadline, it asks for facts no supplier currently publishes,
and the honest answer to most of its fields is "nobody has this data yet". Modelling it as another
export profile would have quietly turned that answer into a validation failure.

What this does **not** do is produce a compliant passport. It produces an audit of the distance to
one, and keeps data gaps (which re-extraction may close) apart from schema gaps (which it never
will).
"""

from axiom.compliance.passport import (
    FieldAssessment,
    FieldStatus,
    PassportField,
    PassportProfile,
    ReadinessReport,
    Requirement,
    assess_readiness,
)
from axiom.compliance.report import (
    ReadinessSweep,
    format_readiness,
    format_readiness_sweep,
    sweep_readiness,
)

__all__ = [
    "FieldAssessment",
    "FieldStatus",
    "PassportField",
    "PassportProfile",
    "ReadinessReport",
    "ReadinessSweep",
    "Requirement",
    "assess_readiness",
    "format_readiness",
    "format_readiness_sweep",
    "sweep_readiness",
]
