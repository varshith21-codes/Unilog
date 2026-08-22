"""Tests for attribute-compatibility equivalence and the cross-reference report.

The property this module exists to guarantee: **an unestablished attribute is never a match.**

That is evidence-or-null one layer up, and getting it wrong is the worst thing this feature could
do. If the candidate's end connection was never extracted, it is *unknown* whether it threads into
the same pipe — and an engine that reported "no difference found, therefore compatible" would ship
a valve that does not fit, with a clean bill of health attached. So an unknown downgrades the
verdict to indeterminate and is named, and "I cannot tell you" stays distinct from "no".

The second property is asymmetry. A 600 psi valve substitutes for a 400 psi one and the reverse is
a downgrade that could fail in service, so `equivalence(a, b)` and `equivalence(b, a)` are
different questions. A symmetric engine would either refuse every safe upgrade or approve every
unsafe downgrade, and there is no third option.

The third is that the middle rung has to exist. *Functional equivalent but not drop-in* is the
answer a buyer needs — a solder-end valve does the same job as a threaded one and will not thread
into the same pipe. "Not compatible" loses a sale for no reason; "compatible" loses a customer.
"""

from __future__ import annotations

import json

import pytest
from axiom.core.evidence import BoundingBox, EvidenceSpan
from axiom.core.product import ProductRecord
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.resolve import (
    Catalogue,
    CatalogueSource,
    Compatibility,
    Verdict,
    cross_reference,
    equivalence,
    format_cross_reference,
    format_equivalence,
    format_sweep,
    rank,
    records_from_bundles,
    records_from_golden,
    sweep,
)
from axiom.schema import load_default
from axiom.schema.models import Interchange, SubstitutionRule

BALL = "PLB.VLV.BALL.2PC"
GATE = "PLB.VLV.GATE.BRZ"
SHA = "9f2c" + "0" * 60


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def golden(registry):
    return records_from_golden(registry)


def span() -> EvidenceSpan:
    return EvidenceSpan(
        span_id="s1",
        document_id="doc",
        document_sha256=SHA,
        quote="stated on the datasheet",
        page=1,
        bbox=BoundingBox(x0=0, y0=0, x1=10, y1=10),
        quote_verified=True,
    )


def value(code: str, canonical, *, display: str | None = None) -> AttributeValue:
    """A publishable, cited value — the only kind equivalence will look at."""
    return AttributeValue(
        attribute_code=code,
        value_raw=display or str(canonical),
        value_canonical=canonical,
        value_display=display,
        method=DerivationMethod.DOCUMENT_EXTRACTION,
        confidence=0.9,
        status=ValueStatus.AUTO_ACCEPTED,
        evidence=[span()],
    )


def record(sku: str, values: dict, *, class_code: str = BALL, brand: str = "Acme") -> ProductRecord:
    product = ProductRecord(tenant_id="t", sku=sku, class_code=class_code, brand=brand)
    for code, canonical in values.items():
        product.add_value(value(code, canonical))
    return product


def psi(magnitude: float) -> Quantity:
    return Quantity(magnitude=magnitude, unit="psi")


def mm(magnitude: float) -> Quantity:
    return Quantity(magnitude=magnitude, unit="mm")


def degc(low: float, high: float) -> ValueRange:
    return ValueRange(minimum=low, maximum=high, unit="degC")


# A complete, mutually-compatible specification to perturb one field at a time.
#
# Every code here is bound to the ball valve class, which matters: an attribute the class does not
# bind comes back INAPPLICABLE and cannot move a verdict, so building a fixture out of unbound
# attributes would produce tests that pass for the wrong reason. `prop65_warning_required` and
# `rohs_compliant` are bound to neither class in this schema and are therefore exercised through
# `_compare_attribute` instead.
BASE = {
    "nominal_size": mm(19.05),
    "pressure_rating_wog": psi(600),
    "steam_pressure_rating": psi(150),
    "temperature_range": degc(-29.0, 186.0),
    "body_material": "Bronze C84400",
    "seat_material": "RPTFE",
    "stem_material": "Brass",
    "port_type": "Full Port",
    "end_connection": "NPT Threaded",
    "number_of_pieces": "Two-Piece",
    "handle_type": "Lever",
    "cv_flow_coefficient": 40.0,
    "approvals": ["UL", "NSF-61"],
    "lead_free_compliant": False,
    "potable_water_approved": True,
}


def verdict_for(registry, overrides: dict, *, candidate_class: str = BALL) -> Verdict:
    reference = record("REF-1", BASE)
    candidate = record("CAND-1", {**BASE, **overrides}, class_code=candidate_class)
    return equivalence(reference, candidate, registry).verdict


# ---------------------------------------------------------------- the ladder


class TestVerdictLadder:
    def test_an_exact_match_is_identical(self, registry):
        assert verdict_for(registry, {}) is Verdict.IDENTICAL

    def test_a_defining_difference_ends_it(self, registry):
        """No amount of agreement elsewhere rescues a different size."""
        assert verdict_for(registry, {"nominal_size": mm(25.4)}) is Verdict.NOT_EQUIVALENT

    def test_a_lower_rating_is_not_a_substitute(self, registry):
        assert (
            verdict_for(registry, {"pressure_rating_wog": psi(400)}) is Verdict.NOT_EQUIVALENT
        )

    def test_a_higher_rating_is_a_drop_in(self, registry):
        """The safe direction. Equality would have refused this."""
        assert verdict_for(registry, {"pressure_rating_wog": psi(1000)}) is Verdict.DROP_IN

    def test_a_form_difference_is_functional_equivalence(self, registry):
        """The rung a binary compatible flag cannot express."""
        assert (
            verdict_for(registry, {"end_connection": "Solder"})
            is Verdict.FUNCTIONAL_EQUIVALENT
        )

    def test_a_material_difference_is_not_equivalent(self, registry):
        """Alloys are compared for equality on purpose — see the schema comment."""
        assert (
            verdict_for(registry, {"body_material": "Bronze C89833"})
            is Verdict.NOT_EQUIVALENT
        )

    def test_a_cosmetic_difference_still_drops_in(self, registry):
        assert verdict_for(registry, {"handle_type": "Tee"}) is Verdict.DROP_IN

    def test_a_defining_difference_outranks_an_unknown(self, registry):
        """A definite negative settles the question whatever else is missing."""
        candidate = {**BASE, "nominal_size": mm(25.4)}
        del candidate["pressure_rating_wog"]
        report = equivalence(record("REF-1", BASE), record("C", candidate), registry)
        assert report.verdict is Verdict.NOT_EQUIVALENT

    def test_an_unmet_requirement_outranks_an_unknown(self, registry):
        candidate = {**BASE, "pressure_rating_wog": psi(400)}
        del candidate["seat_material"]
        report = equivalence(record("REF-1", BASE), record("C", candidate), registry)
        assert report.verdict is Verdict.NOT_EQUIVALENT


class TestUnknownIsNeverAMatch:
    """The property this feature lives or dies on."""

    def test_a_missing_functional_value_forces_indeterminate(self, registry):
        candidate = {k: v for k, v in BASE.items() if k != "pressure_rating_wog"}
        report = equivalence(record("REF-1", BASE), record("C", candidate), registry)
        assert report.verdict is Verdict.INDETERMINATE
        assert "pressure_rating_wog" in {c.attribute_code for c in report.unknown()}

    def test_a_missing_fit_value_withholds_the_drop_in_claim(self, registry):
        """Everything performs, but fit is unconfirmed. Not a drop-in, and not a difference."""
        candidate = {k: v for k, v in BASE.items() if k != "end_connection"}
        report = equivalence(record("REF-1", BASE), record("C", candidate), registry)
        assert report.verdict is Verdict.INDETERMINATE
        assert "end_connection" in {c.attribute_code for c in report.unknown()}

    def test_indeterminate_is_not_a_rejection(self, registry):
        candidate = {k: v for k, v in BASE.items() if k != "pressure_rating_wog"}
        report = equivalence(record("REF-1", BASE), record("C", candidate), registry)
        assert report.verdict.needs_enrichment
        assert not report.verdict.is_substitutable
        assert "enrich" in report.reason.lower()

    def test_an_unknown_critical_does_not_lower_a_capped_verdict(self, registry):
        """Already only a functional equivalent, so resolving the unknown could not raise it."""
        candidate = {**BASE, "port_type": "Reduced Port"}
        del candidate["end_connection"]
        report = equivalence(record("REF-1", BASE), record("C", candidate), registry)
        assert report.verdict is Verdict.FUNCTIONAL_EQUIVALENT

    def test_a_queued_value_counts_as_absent(self, registry):
        """A queued value is a machine's unreviewed guess. Letting one support a substitution
        would launder an unverified extraction into a purchasing decision."""
        candidate = record("C", {k: v for k, v in BASE.items() if k != "pressure_rating_wog"})
        queued = value("pressure_rating_wog", psi(600))
        queued.status = ValueStatus.QUEUED_FOR_REVIEW
        candidate.add_value(queued)

        report = equivalence(record("REF-1", BASE), candidate, registry)
        assert report.verdict is Verdict.INDETERMINATE
        assert "pressure_rating_wog" in {c.attribute_code for c in report.unknown()}

    def test_an_uncited_value_counts_as_absent(self, registry):
        """`is_publishable` already refuses an extraction with no verified evidence. Asserted
        here because equivalence relies on that and must not re-derive it."""
        candidate = record("C", {k: v for k, v in BASE.items() if k != "body_material"})
        uncited = AttributeValue(
            attribute_code="body_material",
            value_canonical="Bronze C84400",
            method=DerivationMethod.DOCUMENT_EXTRACTION,
            confidence=0.9,
            status=ValueStatus.AUTO_ACCEPTED,
            evidence=[
                EvidenceSpan(
                    span_id="s2",
                    document_id="doc",
                    document_sha256=SHA,
                    quote="unverified",
                    quote_verified=False,
                )
            ],
        )
        candidate.add_value(uncited)
        report = equivalence(record("REF-1", BASE), candidate, registry)
        assert "body_material" in {c.attribute_code for c in report.unknown()}

    def test_both_sides_missing_is_neither_match_nor_difference(self, registry):
        trimmed = {k: v for k, v in BASE.items() if k != "cv_flow_coefficient"}
        report = equivalence(record("A", trimmed), record("B", trimmed), registry)
        cv = next(c for c in report.comparisons if c.attribute_code == "cv_flow_coefficient")
        assert cv.compatibility is Compatibility.UNKNOWN_BOTH
        assert not cv.compatibility.is_satisfied
        assert not cv.compatibility.blocks

    def test_an_unknown_reference_imposes_no_requirement(self, registry):
        """If the reference never stated a rating, the candidate cannot fail to meet it."""
        reference = {k: v for k, v in BASE.items() if k != "cv_flow_coefficient"}
        report = equivalence(record("A", reference), record("B", BASE), registry)
        cv = next(c for c in report.comparisons if c.attribute_code == "cv_flow_coefficient")
        assert cv.compatibility is Compatibility.UNKNOWN_REFERENCE
        assert report.verdict is Verdict.INDETERMINATE


class TestDirectionality:
    def test_the_two_directions_disagree(self, registry):
        weak = record("WEAK", {**BASE, "pressure_rating_wog": psi(400)})
        strong = record("STRONG", BASE)

        assert equivalence(weak, strong, registry).verdict is Verdict.DROP_IN
        assert equivalence(strong, weak, registry).verdict is Verdict.NOT_EQUIVALENT

    def test_at_most_inverts_the_direction(self, registry):
        """`prop65_warning_required` is the one attribute where more is worse.

        Exercised through ``_compare_attribute`` rather than two records, because neither valve
        class binds this attribute — a record-level test would come back INAPPLICABLE and pass
        without ever evaluating the direction.
        """
        from axiom.resolve.equivalence import _compare_attribute

        definition = registry.attribute("prop65_warning_required")
        assert definition.substitution is SubstitutionRule.AT_MOST

        clean = value("prop65_warning_required", False)
        warned = value("prop65_warning_required", True)

        # A part needing no warning is an acceptable stand-in for one that does.
        assert (
            _compare_attribute(definition, warned, clean).compatibility
            is Compatibility.SATISFIES
        )
        # The reverse is not.
        assert (
            _compare_attribute(definition, clean, warned).compatibility
            is Compatibility.DIFFERS
        )

    def test_a_range_must_enclose_not_merely_overlap(self, registry):
        """A higher ceiling does not excuse giving up range at the bottom.

        Comparing either bound alone would approve exactly this swap, which is why
        `temperature_range` declares `encloses` rather than `at_least`.
        """
        assert (
            verdict_for(registry, {"temperature_range": degc(-10.0, 200.0)})
            is Verdict.NOT_EQUIVALENT
        )
        assert (
            verdict_for(registry, {"temperature_range": degc(-40.0, 200.0)}) is Verdict.DROP_IN
        )

    def test_approvals_must_be_a_superset(self, registry):
        assert (
            verdict_for(registry, {"approvals": ["UL"]}) is Verdict.NOT_EQUIVALENT
        )
        assert (
            verdict_for(registry, {"approvals": ["UL", "NSF-61", "NSF-372"]}) is Verdict.DROP_IN
        )

    def test_a_compliance_flag_may_be_exceeded_but_not_dropped(self, registry):
        assert verdict_for(registry, {"lead_free_compliant": True}) is Verdict.DROP_IN
        assert (
            verdict_for(registry, {"potable_water_approved": False}) is Verdict.NOT_EQUIVALENT
        )

    def test_mismatched_units_are_not_comparable_rather_than_equal(self, registry):
        candidate = record("C", {**BASE, "pressure_rating_wog": Quantity(magnitude=41, unit="bar")})
        report = equivalence(record("REF-1", BASE), candidate, registry)
        rating = next(
            c for c in report.comparisons if c.attribute_code == "pressure_rating_wog"
        )
        assert rating.compatibility is Compatibility.NOT_COMPARABLE
        assert report.verdict is Verdict.NOT_EQUIVALENT


class TestClassHandling:
    def test_a_class_difference_caps_the_verdict(self, registry):
        """A gate valve is not a drop-in for a ball valve even when every attribute agrees.

        The class carries throttling behaviour and flow characteristic that no attribute here
        models, so a drop-in claim would rest on the absence of attributes.
        """
        verdict = verdict_for(registry, {}, candidate_class=GATE)
        assert verdict is Verdict.FUNCTIONAL_EQUIVALENT

    def test_the_reason_names_both_classes(self, registry):
        report = equivalence(
            record("REF-1", BASE), record("C", BASE, class_code=GATE), registry
        )
        assert BALL in report.reason
        assert GATE in report.reason
        assert not report.same_class

    def test_an_attribute_only_one_class_binds_is_inapplicable_not_unknown(self, registry):
        """A gate valve has no port type in the ball-valve sense. Calling that a gap would make
        every cross-class comparison indeterminate for a reason unrelated to data quality."""
        report = equivalence(
            record("REF-1", BASE), record("C", BASE, class_code=GATE), registry
        )
        assert "port_type" in report.inapplicable()
        port = next(c for c in report.comparisons if c.attribute_code == "port_type")
        assert port.compatibility is Compatibility.INAPPLICABLE
        assert not port.decides


class TestReporting:
    def test_cosmetic_differences_are_reported_but_do_not_block(self, registry):
        report = equivalence(
            record("REF-1", BASE), record("C", {**BASE, "handle_type": "Tee"}), registry
        )
        assert report.verdict is Verdict.DROP_IN
        assert [c.attribute_code for c in report.cosmetic_differences()] == ["handle_type"]
        assert "handle_type" not in {c.attribute_code for c in report.blocking()}

    def test_an_upgrade_is_reported_separately_from_a_match(self, registry):
        """Exceeding the reference is not the same as agreeing with it."""
        report = equivalence(
            record("REF-1", BASE),
            record("C", {**BASE, "pressure_rating_wog": psi(1000)}),
            registry,
        )
        assert [c.attribute_code for c in report.satisfied()] == ["pressure_rating_wog"]
        assert "pressure_rating_wog" not in {c.attribute_code for c in report.agreed()}

    def test_coverage_note_states_how_much_was_established(self, registry):
        report = equivalence(record("REF-1", BASE), record("C", BASE), registry)
        assert f"{report.compared} of {len(report.deciding())}" in report.coverage_note

    def test_an_unclassified_attribute_is_reported_rather_than_ignored(self, registry):
        """Mirrors variant explosion surfacing an ordering-table column no attribute claimed."""
        from axiom.schema.models import AttributeDefinition, Datatype

        class Patched:
            def __init__(self, inner):
                self._inner = inner

            def attribute(self, code):
                if code == "gtin":
                    return AttributeDefinition(
                        code="gtin",
                        name="GTIN",
                        datatype=Datatype.STRING,
                        description="an unclassified attribute for this test",
                    )
                return self._inner.attribute(code)

            def attributes_for(self, class_code):
                return self._inner.attributes_for(class_code)

        patched = Patched(registry)
        report = equivalence(
            record("A", {**BASE, "gtin": "012345678905"}),
            record("B", {**BASE, "gtin": "012345678929"}),
            patched,
        )
        assert "gtin" in report.unclassified()
        assert report.verdict is Verdict.IDENTICAL

    def test_reports_serialise(self, registry):
        report = equivalence(
            record("REF-1", BASE), record("C", {**BASE, "end_connection": "Solder"}), registry
        )
        payload = json.loads(json.dumps(report.to_dict(), default=str))
        assert payload["verdict"] == "functional_equivalent"
        assert payload["substitutable"] is True
        assert payload["blocking_detail"][0]["attribute_code"] == "end_connection"

    def test_formatters_run_on_real_data(self, registry, golden):
        text = format_equivalence(
            equivalence(golden.get("77C-105R"), golden.get("77C-105"), registry)
        )
        assert "EQUIVALENCE" in text
        assert "77C-105" in text
        assert "CROSS-REFERENCE" in format_cross_reference(
            cross_reference("BA-100-100", golden, registry)
        )
        assert "SWEEP" in format_sweep(sweep(golden, registry))


class TestRanking:
    def test_the_reference_never_ranks_against_itself(self, registry, golden):
        reports = rank(
            golden.get("BA-100-075"), golden.records, registry, include_unsubstitutable=True
        )
        assert "BA-100-075" not in {r.candidate_sku for r in reports}
        assert len(reports) == len(golden) - 1

    def test_better_verdicts_sort_first(self, registry):
        reference = record("REF-1", BASE)
        candidates = [
            record("WORSE", {**BASE, "pressure_rating_wog": psi(400)}),
            record("FITS", {**BASE, "end_connection": "Solder"}),
            record("SAME", BASE),
        ]
        ranked = rank(reference, candidates, registry, include_unsubstitutable=True)
        assert [r.candidate_sku for r in ranked] == ["SAME", "FITS", "WORSE"]

    def test_unsubstitutable_candidates_are_excluded_by_default(self, registry):
        reference = record("REF-1", BASE)
        candidates = [record("WORSE", {**BASE, "pressure_rating_wog": psi(400)})]
        assert rank(reference, candidates, registry) == []

    def test_a_better_established_candidate_outranks_a_thinner_one(self, registry):
        """Two drop-ins are not equally good news if one was judged on far less."""
        reference = record("REF-1", BASE)
        thin = {k: v for k, v in BASE.items() if k not in {"cv_flow_coefficient", "approvals"}}
        candidates = [record("THIN", thin), record("FULL", BASE)]
        ranked = rank(reference, candidates, registry, include_unsubstitutable=True)
        assert ranked[0].candidate_sku == "FULL"


class TestSweep:
    @pytest.fixture(scope="class")
    def result(self, registry, golden):
        return sweep(golden, registry)

    def test_every_ordered_pair_is_compared(self, result, golden):
        assert result.pairs == len(golden) * (len(golden) - 1)

    def test_asymmetry_is_present_and_named(self, result):
        """If this were ever empty on a corpus with mixed ratings and ports, the engine would
        have quietly become symmetric."""
        asymmetric = result.asymmetric_pairs()
        assert asymmetric
        pairs = {tuple(sorted((f.reference_sku, f.candidate_sku))) for f, _ in asymmetric}
        assert ("77C-105", "77C-105R") in pairs

    def test_comparable_pairs_exclude_trivial_size_mismatches(self, result):
        """A 1/4" valve not replacing a 2" valve is arithmetic, not a finding."""
        assert 0 < len(result.comparable()) < result.pairs

    def test_blocking_attributes_are_ranked(self, result):
        blockers = dict(result.blocking_attributes())
        assert blockers["nominal_size"] > blockers.get("cv_flow_coefficient", 0)

    def test_the_schema_classifies_every_compared_attribute(self, result):
        assert result.unclassified() == []

    def test_the_sweep_serialises(self, result):
        payload = json.loads(json.dumps(result.to_dict(), default=str))
        assert payload["source"] == "golden"
        assert payload["measured"] is False
        assert payload["pairs"] == result.pairs
        assert payload["asymmetry_detail"]


class TestCatalogue:
    def test_golden_records_are_publishable_and_human_sourced(self, golden):
        """Ground truth really is human-entered, and that family publishes without a citation
        because a named person is an accountable source."""
        assert len(golden) == 15
        assert not golden.failures
        record_ = golden.get("BA-100-075")
        assert record_.publishable_values()
        for item in record_.publishable_values():
            assert item.method is DerivationMethod.HUMAN_ENTRY

    def test_golden_catalogue_is_marked_unmeasured(self, golden):
        assert golden.source is CatalogueSource.GOLDEN
        assert not golden.source.is_measured
        assert "hand-authored" in golden.source.note

    def test_bundles_rebuild_into_records(self, registry):
        from pathlib import Path

        catalogue = records_from_bundles(
            Path(__file__).resolve().parents[1] / "data" / "console", registry
        )
        assert catalogue.source is CatalogueSource.PIPELINE
        assert catalogue.source.is_measured
        assert len(catalogue) >= 2
        record_ = catalogue.get("BA-100-075")
        assert record_ is not None
        assert record_.class_code == BALL
        assert record_.publishable_values()

    def test_a_missing_bundle_directory_is_empty_not_fatal(self, registry):
        from pathlib import Path

        catalogue = records_from_bundles(Path("does/not/exist"), registry)
        assert len(catalogue) == 0
        assert catalogue.skus() == []

    def test_an_empty_catalogue_reports_no_skus(self):
        catalogue = Catalogue(source=CatalogueSource.PIPELINE)
        assert catalogue.get("anything") is None
        assert catalogue.summary()["records"] == 0

    def test_cross_reference_refuses_an_unknown_sku(self, registry, golden):
        with pytest.raises(KeyError, match="not in this catalogue"):
            cross_reference("NOPE-1", golden, registry)


class TestSchemaSemantics:
    def test_every_attribute_declares_an_interchange_level(self, registry):
        """An undeclared attribute is excluded from every verdict, so the omission must not be
        silent. This is the guard that keeps the declaration honest as attributes are added."""
        missing = [c for c in registry.attribute_codes if registry.attribute(c).interchange is None]
        assert missing == []

    def test_directional_rules_only_sit_on_deciding_attributes(self, registry):
        for code in registry.attribute_codes:
            definition = registry.attribute(code)
            if definition.substitution.is_directional:
                assert definition.interchange is not None
                assert definition.interchange is not Interchange.COSMETIC

    def test_defining_is_reserved_for_physical_impossibility(self, registry):
        """`defining` ends the conversation, so the bar is impossibility rather than importance.

        A difference here cannot be rescued by agreement anywhere else, which makes it the one
        level that must not be handed out for being merely consequential. Every holder passes the
        same test: either the parts cannot be made to connect, or the two things are not the same
        kind of product at all.

        The two original holders, kept as the reference cases:

        * `nominal_size` — a 3/4" valve does not join 1" pipe at any price.
        * `lamp_base` — a GU10 lamp does not enter an E26 socket. Binary, and physical.

        `lamp_shape` was declared defining first and then demoted, which is the useful precedent.
        PAR38 versus MR16 looks disqualifying, but that pair also differs in base, so `lamp_base`
        already refuses it; what shape alone separates is A19 from A21, which share a base and
        substitute for each other daily. Defining would have refused a real substitution in order
        to catch a case another attribute catches.

        Adding a code here is a claim of that kind. Extend the list and the docstring together.

        --------------------------------------------------------------------------------------
        The catalogue-wide taxonomy added twenty-six more, in four groups. They are grouped
        because the justification is per-group rather than per-attribute, and because the grouping
        is what makes a wrong addition visible.

        **It will not mount.** A mechanical interface that either mates or does not:
        `arbor_size`, `attachment_type`, `shank_type`, `drive_size`, `bit_tip_type`,
        `battery_platform`, `nema_configuration`, `gang_count`, `shank_diameter`.
        A 7/8" arbor wheel does not go on a 5/8" spindle; a PSA disc does not stick to a
        hook-and-loop pad; an M18 pack does not latch onto an 18V LXT tool; a 1-gang plate does not
        cover a 2-gang box; a .131" nail does not feed a magazine cut for .113".

        **It will not fit the opening.** A dimension the surrounding assembly fixes:
        `wheel_diameter`, `blade_diameter`, `appliance_width`, `handing`, `nominal_dimensions`,
        `garment_size`.
        A 12" wheel does not fit a 5" grinder's guard; a 36" range does not enter a 30" cabinet;
        a left-hand door does not hang in a right-hand opening, and the opening is cut before anyone
        finds out.

        **It is a different kind of thing.** Not a variant to be traded off but a separate product,
        usually bought alongside rather than instead of the other:
        `wiring_device_type`, `enclosure_form`, `railing_component`, `roofing_component`,
        `ppe_form`, `fastener_type`, `abrasive_form`, `cable_type`, `conductor_count`.
        A dimmer is not a wall plate; a post is not a baluster; a shingle is not underlayment; a
        glove liner is worn under a glove rather than instead of one; SO cord is not building wire
        and the listing that says otherwise is a code violation.

        **The connection is to a different utility.** `fuel_type`, `edge_profile`.
        A gas range cannot be fed from a 240 V circuit and an electric one cannot be fed from a gas
        line. A grooved deck board takes hidden fasteners in the groove and a square-edge board must
        be face-screwed; neither accepts the other's system.

        --------------------------------------------------------------------------------------
        Eleven candidates were considered and DEMOTED to `critical` while this list was drawn up,
        which is the part worth keeping. Each was important — several were the most commercially
        consequential field in their cohort — and importance is not the bar:

        `abrasive_operation` (a cut-off and a grinding wheel of the same size mount on the same
        grinder; the danger is in the use, not the fit), `tool_configuration` (bare versus kit is a
        3x price difference on an identical tool), `power_source`, `abrasive_grit`, `bit_tip_size`,
        `panel_thickness`, `blade_span`, `wire_gauge`, `heated_garment`, `fastener_length`,
        `measurement_range`.
        """
        defining = [
            c
            for c in registry.attribute_codes
            if registry.attribute(c).interchange is Interchange.DEFINING
        ]
        # Compared against the sorted literal so the groups above can stay in reading order. The
        # grouping is the point of this list — an addition that does not fit one of the four
        # headings is an addition that has not been justified.
        assert defining == sorted([
            # it will not mount
            "arbor_size",
            "attachment_type",
            "battery_platform",
            "bit_tip_type",
            "drive_size",
            "gang_count",
            "nema_configuration",
            "shank_diameter",
            "shank_type",
            # it will not fit the opening
            "appliance_width",
            "blade_diameter",
            "garment_size",
            "handing",
            "nominal_dimensions",
            "wheel_diameter",
            # it is a different kind of thing
            "abrasive_form",
            "cable_type",
            "conductor_count",
            "enclosure_form",
            "fastener_type",
            "ppe_form",
            "railing_component",
            "roofing_component",
            "wiring_device_type",
            # a different utility
            "edge_profile",
            "fuel_type",
            # the two original holders
            "lamp_base",
            "nominal_size",
        ])

    def test_ranges_use_enclosure_and_sets_use_superset(self, registry):
        assert registry.attribute("temperature_range").substitution is SubstitutionRule.ENCLOSES
        assert registry.attribute("approvals").substitution is SubstitutionRule.SUPERSET

    def test_materials_are_compared_for_equality_not_ranked(self, registry):
        """Ranking alloys would let a substitution be approved on a string sort."""
        for code in ("body_material", "seat_material", "stem_material"):
            assert registry.attribute(code).substitution is SubstitutionRule.EQUAL

    def test_series_is_cosmetic_or_nothing_would_ever_cross_reference(self, registry):
        assert registry.attribute("product_series").interchange is Interchange.COSMETIC
