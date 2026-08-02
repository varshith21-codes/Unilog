"""Core domain model for AXIOM.

This package defines the canonical product record and — critically — encodes the
"evidence or null" principle as a *structural* constraint rather than a convention.
See `axiom.core.values.AttributeValue`: any value produced by an extraction-family
method is rejected at construction time unless it carries at least one evidence span.
"""

from axiom.core.certificate import CertificateSummary, EnrichmentCertificate, QualityIndex
from axiom.core.evidence import EvidenceSpan, SourceDocument
from axiom.core.gaps import Gap, GapReason
from axiom.core.product import Classification, ProductRecord
from axiom.core.validation import ValidationLayer, ValidationResult, Verdict
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)

__all__ = [
    "AttributeValue",
    "CertificateSummary",
    "Classification",
    "DerivationMethod",
    "EnrichmentCertificate",
    "EvidenceSpan",
    "Gap",
    "GapReason",
    "ProductRecord",
    "Quantity",
    "QualityIndex",
    "SourceDocument",
    "ValidationLayer",
    "ValidationResult",
    "ValueRange",
    "ValueStatus",
    "Verdict",
]
