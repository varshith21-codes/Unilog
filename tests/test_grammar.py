"""Tests for part-number grammar induction.

The property this module exists to guarantee: **an induced rule that cannot be told apart from a
coincidence is refused, not ranked lower.**

Induction always succeeds. Given four part numbers with four distinct size codes, "the third
segment determines the size" is a perfect rule — and so is "the third segment determines the
carton quantity", and so is "the third segment determines the price". With one observation per
key, every attribute is trivially a function of every varying segment, and every such rule fits
its training data exactly while predicting nothing. That is the failure this module is built
around, and it is silent: the rule count goes up, the report looks better, and held-out accuracy
is not *low* but *undefined*, because the key is simply absent.

The second property is the distinction between an encoding and a sequence. `BA-100-075` really is
0.75 inches in hundredths, so a rule learned from it extends to a code never seen. `77C-104` is
3/4" only because a catalogue says so, and nothing about `104` implies it. Both look identical in
a lookup table. Only one generalises, and a system that cannot tell them apart will claim coverage
it does not have.

The third is that a part number is never a compliance declaration, at any level of support.
"""

from __future__ import annotations

import pytest
from axiom.core.values import (
    AttributeValue,
    DerivationMethod,
    Quantity,
    ValueRange,
    ValueStatus,
)
from axiom.evaluation import GoldenSet
from axiom.evaluation.grammar import observations_from_golden, run_held_out
from axiom.extract.grammar import (
    Observation,
    PartNumberGrammar,
    RejectionReason,
    RuleKind,
    SegmentKind,
    SegmentRule,
    induce,
    segment,
    shape,
)
from axiom.schema import load_default

BALL_CLASS = "PLB.VLV.BALL.2PC"


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def golden():
    return GoldenSet.load_default()


@pytest.fixture(scope="module")
def golden_observations(golden, registry):
    observations, failures = observations_from_golden(golden, registry)
    assert not failures, f"the golden set itself failed to normalise: {failures}"
    return observations


def size(inches_mm: float) -> Quantity:
    return Quantity(magnitude=inches_mm, unit="mm")


# ---------------------------------------------------------------- segmentation


class TestSegmentation:
    def test_splits_on_separators_and_alpha_numeric_transitions(self):
        assert [s.text for s in segment("BA-100-075")] == ["BA", "100", "075"]
        assert [s.text for s in segment("77C-105R")] == ["77", "C", "105", "R"]

    def test_separators_do_not_occupy_positions(self):
        """Which separator a catalogue uses must not shift the position of the size code."""
        assert [s.text for s in segment("BA.100.075")] == ["BA", "100", "075"]
        assert segment("BA-100-075")[2].text == segment("BA.100.075")[2].text

    def test_an_unseparated_run_cannot_be_subdivided(self):
        """`BA100075` is two tokens, not three, and that is the honest answer.

        Nothing in the string says whether the size code is two digits or three; recovering that
        needs a grammar already known for the series, which is the problem this module is solving
        rather than one it may assume away. The limitation is documented on `segment` rather than
        papered over with a guess at field widths.
        """
        assert [s.text for s in segment("BA100075")] == ["BA", "100075"]

    def test_kinds_are_classified(self):
        segments = segment("77C-105R")
        assert [s.kind for s in segments] == [
            SegmentKind.NUMERIC,
            SegmentKind.ALPHA,
            SegmentKind.NUMERIC,
            SegmentKind.ALPHA,
        ]
        assert segments[0].as_int == 77
        assert segments[1].as_int is None

    def test_shape_groups_two_manufacturers_together(self):
        """Different suppliers sharing a layout must land in one group, so a rule can be
        corroborated by two independent catalogues rather than one."""
        assert shape("BA-100-075") == shape("T-113-025") == "A-N-N"

    def test_shape_separates_a_suffix_into_its_own_group(self):
        """The reduced-port suffix must not contaminate its full-port twin."""
        assert shape("77C-105") == "NA-N"
        assert shape("77C-105R") == "NA-NA"

    def test_shape_preserves_separators_verbatim(self):
        assert shape("BA/100/075") == "A/N/N"
        assert shape("BA-100-075") != shape("BA/100/075")

    def test_empty_and_separator_only_input_is_harmless(self):
        assert segment("") == ()
        assert segment("---") == ()
        assert shape("") == ""


# ---------------------------------------------------------------- linear rules


class TestLinearRules:
    """The only rule kind that predicts a token it has never seen."""

    def test_discovers_the_hundredths_of_an_inch_encoding(self, registry, golden_observations):
        grammar = induce(golden_observations, registry)
        linear = [
            r
            for r in grammar.rules
            if r.kind is RuleKind.LINEAR and r.attribute_code == "nominal_size"
        ]
        assert len(linear) == 1
        rule = linear[0]
        assert rule.shape == "A-N-N"
        assert rule.position == 2
        assert rule.scale == pytest.approx(0.254)
        assert rule.unit == "mm"
        assert rule.generalises

    def test_predicts_a_code_absent_from_the_training_set(self, registry, golden_observations):
        """The whole point of a linear rule. `BA-100-150` is in no datasheet here."""
        grammar = induce(golden_observations, registry)
        reading = grammar.predict("BA-100-150")
        assert reading.predictions["nominal_size"].value_canonical == size(38.1)
        assert reading.predictions["nominal_size"].generalises

    def test_magnitudes_are_exact_not_merely_close(self, registry, golden_observations):
        """`nominal_size` declares tolerance 0.0, so float drift is a wrong value.

        `6.35 / 25 * 75` in binary floating point is 19.049999999999997, which would be scored
        as a fabricated size rather than a correct one. The scale is derived and applied in
        Decimal precisely so this assertion can be `==`.
        """
        grammar = induce(golden_observations, registry)
        reading = grammar.predict("BA-100-075")
        assert reading.predictions["nominal_size"].value_canonical == size(19.05)

    def test_refuses_a_sequence_that_is_not_an_encoding(self, registry, golden_observations):
        """77C sizes run 103, 104, 105, 106 for 1/2", 3/4", 1", 1-1/4".

        Consecutive catalogue numbers, not a measurement. No linear rule can fit them, and the
        lookup that would is uncorroborated, so the grammar must decline to size a 77C part.
        """
        grammar = induce(golden_observations, registry)
        assert not [
            r
            for r in grammar.rules_for("NA-N")
            if r.attribute_code == "nominal_size"
        ]
        reading = grammar.predict("77C-105")
        assert "nominal_size" not in reading.predictions

    def test_requires_three_distinct_tokens(self, registry):
        """Two points through the origin can agree by luck; the rule has to be tested."""
        two = [
            Observation("X-1-025", {"nominal_size": size(6.35)}),
            Observation("X-1-050", {"nominal_size": size(12.7)}),
        ]
        assert not [r for r in induce(two, registry).rules if r.kind is RuleKind.LINEAR]

        three = [*two, Observation("X-1-100", {"nominal_size": size(25.4)})]
        linear = [r for r in induce(three, registry).rules if r.kind is RuleKind.LINEAR]
        assert len(linear) == 1
        assert linear[0].scale == pytest.approx(0.254)

    def test_inconsistent_scale_is_not_linear(self, registry):
        observations = [
            Observation("X-1-025", {"nominal_size": size(6.35)}),
            Observation("X-1-050", {"nominal_size": size(12.7)}),
            Observation("X-1-100", {"nominal_size": size(99.0)}),
        ]
        assert not [r for r in induce(observations, registry).rules if r.kind is RuleKind.LINEAR]

    def test_mixed_units_are_not_linear(self, registry):
        observations = [
            Observation("X-1-025", {"nominal_size": Quantity(magnitude=6.35, unit="mm")}),
            Observation("X-1-050", {"nominal_size": Quantity(magnitude=0.5, unit="in")}),
            Observation("X-1-100", {"nominal_size": Quantity(magnitude=25.4, unit="mm")}),
        ]
        assert not [r for r in induce(observations, registry).rules if r.kind is RuleKind.LINEAR]


# ---------------------------------------------------------------- the guards


class TestCoincidenceGuard:
    """A bijection over distinct observations is indistinguishable from coincidence."""

    def test_uncorroborated_lookup_is_refused(self, registry):
        """Four part numbers, four carton quantities, every key seen once.

        A perfect rule that predicts nothing. `case_quantity` is used rather than a size because
        it is genuinely not encoded in a part number, so admitting this would be pure overfitting.
        """
        observations = [
            Observation("Z-1-011", {"case_quantity": 24}),
            Observation("Z-1-012", {"case_quantity": 12}),
            Observation("Z-1-013", {"case_quantity": 6}),
            Observation("Z-1-014", {"case_quantity": 4}),
        ]
        grammar = induce(observations, registry)
        assert not [r for r in grammar.rules if r.attribute_code == "case_quantity"]
        reasons = {r.reason for r in grammar.rejected if r.attribute_code == "case_quantity"}
        assert RejectionReason.UNCORROBORATED in reasons

    def test_dropping_the_guard_admits_it(self, registry):
        """Recorded so the guard's effect is demonstrable rather than asserted."""
        observations = [
            Observation("Z-1-011", {"case_quantity": 24}),
            Observation("Z-1-012", {"case_quantity": 12}),
            Observation("Z-1-013", {"case_quantity": 6}),
            Observation("Z-1-014", {"case_quantity": 4}),
        ]
        rules = induce(observations, registry, min_support=1).rules
        lookups = [r for r in rules if r.attribute_code == "case_quantity"]
        assert lookups and all(r.kind is RuleKind.LOOKUP for r in lookups)

    def test_a_repeated_key_is_corroboration(self, registry):
        """Two independent series agreeing that `025` means a quarter inch is evidence."""
        observations = [
            Observation("P-1-025", {"case_quantity": 24}),
            Observation("Q-2-025", {"case_quantity": 24}),
            Observation("P-1-050", {"case_quantity": 12}),
            Observation("Q-2-050", {"case_quantity": 12}),
        ]
        grammar = induce(observations, registry)
        lookup = [
            r
            for r in grammar.rules
            if r.attribute_code == "case_quantity" and r.kind is RuleKind.LOOKUP
        ]
        assert lookup
        assert max(r.corroboration for r in lookup) == 2

    def test_one_token_two_values_is_not_a_function(self, registry):
        """`025` meaning 24 in one series and 20 in another means the position cannot decide."""
        observations = [
            Observation("P-1-025", {"case_quantity": 24}),
            Observation("Q-2-025", {"case_quantity": 20}),
            Observation("P-1-050", {"case_quantity": 24}),
            Observation("Q-2-050", {"case_quantity": 20}),
        ]
        grammar = induce(observations, registry)
        rejected = [
            r
            for r in grammar.rejected
            if r.attribute_code == "case_quantity" and r.position == 2
        ]
        assert rejected
        assert rejected[0].reason is RejectionReason.NOT_A_FUNCTION

    def test_a_single_observation_yields_nothing_and_is_not_reported_as_refused(self, registry):
        """One example is an absence of data, not a refused candidate."""
        grammar = induce([Observation("P-1-025", {"case_quantity": 24})], registry)
        assert grammar.rules == ()
        assert not [r for r in grammar.rejected if r.reason is RejectionReason.NOT_A_FUNCTION]


class TestComplianceRefusal:
    """A part number is not a declaration of conformity."""

    @pytest.mark.parametrize(
        "code, value",
        [
            ("lead_free_compliant", True),
            ("potable_water_approved", True),
            ("country_of_origin", "United States"),
        ],
    )
    def test_unanimous_agreement_is_still_refused(self, registry, code, value):
        observations = [
            Observation(f"P-1-{n:03d}", {code: value}) for n in (25, 50, 75, 100)
        ]
        grammar = induce(observations, registry)
        assert not [r for r in grammar.rules if r.attribute_code == code]
        rejected = [r for r in grammar.rejected if r.attribute_code == code]
        assert rejected and rejected[0].reason is RejectionReason.COMPLIANCE_CLAIM

    def test_refusal_is_recorded_once_per_shape_not_once_per_position(self, registry):
        """Otherwise six compliance attributes across four positions would bury the real
        refusals under twenty-four duplicates."""
        observations = [
            Observation(f"P-1-{n:03d}", {"lead_free_compliant": True}) for n in (25, 50, 75)
        ]
        rejected = [
            r
            for r in induce(observations, registry).rejected
            if r.attribute_code == "lead_free_compliant"
        ]
        assert len(rejected) == 1
        assert rejected[0].position is None

    def test_the_real_corpus_never_grammatically_claims_compliance(
        self, registry, golden_observations
    ):
        grammar = induce(golden_observations, registry)
        for rule in grammar.rules:
            definition = registry.attribute(rule.attribute_code)
            assert not definition.compliance_claim, (
                f"rule {rule.describe()} would derive the compliance claim "
                f"{rule.attribute_code!r} from a part number"
            )


# ---------------------------------------------------------------- prediction


class TestPrediction:
    def test_unknown_shape_reads_as_unmatched_rather_than_empty(
        self, registry, golden_observations
    ):
        """"No grammar for this catalogue" and "a grammar that found nothing" are different
        states, and only the first one means the part number is unrecognised."""
        grammar = induce(golden_observations, registry)
        reading = grammar.predict("WIDGET/44/XY/9")
        assert reading.matched is False
        assert reading.predictions == {}

    def test_agreeing_positions_are_counted_as_corroboration(
        self, registry, golden_observations
    ):
        """In this corpus the alpha prefix and the family number co-vary perfectly, so the
        series is identified twice. That is agreement from within the part number itself."""
        grammar = induce(golden_observations, registry)
        reading = grammar.predict("BA-100-075")
        assert reading.predictions["product_series"].agreeing_rules >= 2

    def test_disagreeing_rules_abstain_instead_of_guessing(self, registry):
        """Two positions contradicting each other is where guessing is least defensible."""
        grammar = PartNumberGrammar(
            rules=(
                SegmentRule(
                    shape="A-N",
                    position=0,
                    attribute_code="port_type",
                    kind=RuleKind.CONSTANT,
                    mapping={"P": "Full Port"},
                    support=2,
                    corroboration=2,
                ),
                SegmentRule(
                    shape="A-N",
                    position=1,
                    attribute_code="port_type",
                    kind=RuleKind.CONSTANT,
                    mapping={"9": "Reduced Port"},
                    support=2,
                    corroboration=2,
                ),
            ),
            tolerances={"port_type": 0.0},
        )
        reading = grammar.predict("P-9")
        assert "port_type" not in reading.predictions
        assert [c.attribute_code for c in reading.conflicts] == ["port_type"]
        assert len(reading.conflicts[0].candidates) == 2

    def test_a_generalising_rule_is_cited_over_a_memorised_one(self, registry):
        """Both agree on the value; the citation should name the rule that had to extrapolate."""
        grammar = PartNumberGrammar(
            rules=(
                SegmentRule(
                    shape="A-N",
                    position=1,
                    attribute_code="nominal_size",
                    kind=RuleKind.LOOKUP,
                    mapping={"100": size(25.4)},
                    support=5,
                    corroboration=5,
                ),
                SegmentRule(
                    shape="A-N",
                    position=1,
                    attribute_code="nominal_size",
                    kind=RuleKind.LINEAR,
                    scale=0.254,
                    unit="mm",
                    support=3,
                    corroboration=1,
                ),
            ),
            tolerances={"nominal_size": 0.0},
        )
        prediction = grammar.predict("X-100").predictions["nominal_size"]
        assert prediction.rule.kind is RuleKind.LINEAR
        assert prediction.generalises


# ---------------------------------------------------------------- emitted values


class TestEmittedValues:
    def test_values_are_queued_and_marked_as_inference(self, registry, golden_observations):
        grammar = induce(golden_observations, registry)
        values = grammar.values_for("BA-100-075", registry=registry, class_code=BALL_CLASS)
        assert values
        for value in values:
            assert value.method is DerivationMethod.PART_NUMBER_GRAMMAR
            assert value.method.is_inference
            assert value.status is ValueStatus.QUEUED_FOR_REVIEW
            assert not value.is_publishable

    def test_an_inferred_value_cannot_be_constructed_as_auto_accepted(self):
        """The domain model refuses it, so this can never become an auto-publish path.

        Asserted here rather than only in test_core_invariants because the grammar is the first
        producer of inferred values in the system, and the guarantee is what makes it safe to
        run unattended.
        """
        with pytest.raises(ValueError, match="cannot be AUTO_ACCEPTED"):
            AttributeValue(
                attribute_code="nominal_size",
                value_canonical=size(19.05),
                method=DerivationMethod.PART_NUMBER_GRAMMAR,
                confidence=0.99,
                status=ValueStatus.AUTO_ACCEPTED,
            )

    def test_confidence_stays_below_every_measured_threshold(self, registry, golden_observations):
        """The risk policy has selected 0.685 and 0.719. A grammar value must not reach either,
        independently of the status refusal above."""
        grammar = induce(golden_observations, registry)
        values = grammar.values_for("BA-100-075", registry=registry, class_code=BALL_CLASS)
        assert values
        assert max(v.confidence for v in values) < 0.685

    def test_the_token_is_recorded_as_the_raw_value(self, registry, golden_observations):
        """A grammar value has no evidence span, so the token is the only provenance there is."""
        grammar = induce(golden_observations, registry)
        values = {
            v.attribute_code: v
            for v in grammar.values_for("BA-100-075", registry=registry, class_code=BALL_CLASS)
        }
        assert values["nominal_size"].value_raw == "075"
        assert values["nominal_size"].evidence == []

    def test_values_are_filtered_to_the_class(self, registry, golden_observations):
        grammar = induce(golden_observations, registry)
        bound = {a.code for a in registry.attributes_for(BALL_CLASS)}
        values = grammar.values_for("BA-100-075", registry=registry, class_code=BALL_CLASS)
        assert {v.attribute_code for v in values} <= bound

    def test_only_codes_narrows_the_output(self, registry, golden_observations):
        grammar = induce(golden_observations, registry)
        values = grammar.values_for(
            "BA-100-075",
            registry=registry,
            class_code=BALL_CLASS,
            only_codes=["nominal_size"],
        )
        assert [v.attribute_code for v in values] == ["nominal_size"]

    def test_a_hand_built_grammar_cannot_smuggle_a_compliance_value_through(self, registry):
        """Induction never builds such a rule. `values_for` refuses it a second time, so a
        grammar assembled or deserialised by hand cannot bypass the schema."""
        grammar = PartNumberGrammar(
            rules=(
                SegmentRule(
                    shape="A-N",
                    position=0,
                    attribute_code="lead_free_compliant",
                    kind=RuleKind.CONSTANT,
                    mapping={"X": True},
                    support=9,
                    corroboration=9,
                ),
            ),
            tolerances={"lead_free_compliant": 0.0},
        )
        assert grammar.predict("X-1").predictions
        assert grammar.values_for("X-1", registry=registry, class_code=BALL_CLASS) == []

    def test_unmatched_shape_emits_no_values(self, registry, golden_observations):
        grammar = induce(golden_observations, registry)
        assert grammar.values_for("WIDGET/44", registry=registry, class_code=BALL_CLASS) == []


# ---------------------------------------------------------------- held-out validation


class TestHeldOutValidation:
    @pytest.fixture(scope="class")
    def result(self, golden, registry):
        return run_held_out(golden, registry)

    def test_nothing_is_fabricated(self, result):
        """The gate the script exits non-zero on. A grammar costs nothing to run, so a
        fabrication rate above zero would scale faster than any review capacity."""
        assert result.metrics.hallucinated == 0
        assert result.metrics.abstention_correctness == 1.0

    def test_no_wrong_values(self, result):
        assert result.metrics.wrong == 0
        assert result.metrics.precision == 1.0

    def test_recall_is_bounded_by_what_a_part_number_can_carry(self, result):
        """Around half. Reported against every scored attribute rather than a hand-picked
        subset, which is what stops the figure being cosmetic."""
        assert 0.4 < result.metrics.recall < 0.8

    def test_every_fold_was_scored(self, result, golden):
        assert len(result.folds) == len(golden.products)
        assert result.metrics.total == golden.comparison_count

    def test_citation_coverage_is_zero_by_construction(self, result):
        """A part number is not a document. If this ever rises, something has started claiming
        evidence it does not have."""
        assert result.metrics.citation_coverage == 0.0

    def test_a_shape_seen_once_cannot_be_learned_from_itself(self, result):
        """Holding out one of the two reduced-port parts leaves a single sibling, and a
        one-member shape group supports no rule. Reported rather than averaged away."""
        unlearnable = {fold.sku for fold in result.folds_without_grammar}
        assert unlearnable == {"77C-105R", "77C-106R"}

    def test_rules_fired_is_lower_than_rules_claimed(self, result):
        """The honest counterweight to a rule count: co-varying positions inflate it, and a
        rule that never fired on unseen data has demonstrated nothing."""
        assert result.rules_fired <= result.covered_pairs <= len(result.grammar.rules)

    def test_compliance_attributes_are_never_recovered(self, result, registry):
        for code, metrics in result.by_attribute().items():
            if registry.attribute(code).compliance_claim:
                assert metrics.correct == 0, f"{code} was derived from a part number"

    def test_the_full_grammar_is_reported_but_not_scored(self, result):
        """The metrics must come from the folds. A grammar induced on the whole corpus and
        scored against it would be measuring its own training set."""
        assert result.grammar.observations == len(result.folds)
        assert result.metrics.total > 0

    def test_the_guard_does_not_change_accuracy_on_this_corpus(self, golden, registry, result):
        """An uncorroborated rule keys on a token the held-out part does not have, so it
        abstains rather than errs. The guard buys an honest coverage claim, not a better score,
        and the report says so rather than implying otherwise.
        """
        unguarded = run_held_out(golden, registry, min_support=1)
        assert len(unguarded.grammar.rules) > len(result.grammar.rules)
        assert unguarded.metrics.correct == result.metrics.correct
        assert unguarded.metrics.hallucinated == 0

    def test_summary_is_serialisable(self, result):
        import json

        payload = json.loads(json.dumps(result.to_dict(), default=str))
        assert payload["arm"] == "guarded"
        assert payload["golden_set"] == "pvf_valves_v1"
        assert payload["generalising_rules"] >= 1
        assert payload["hallucinated"] == 0


# ---------------------------------------------------------------- rule reporting


class TestRuleReporting:
    def test_every_rule_describes_itself(self, registry, golden_observations):
        for rule in induce(golden_observations, registry).rules:
            assert rule.describe()
            assert rule.attribute_code in rule.describe()

    def test_rules_serialise_with_canonical_values_flattened(self, registry):
        """`Quantity` and `ValueRange` are Pydantic models and must not leak into JSON."""
        import json

        observations = [
            Observation("P-1-025", {"temperature_range": ValueRange(
                minimum=-20.0, maximum=100.0, unit="degC"
            )}),
            Observation("P-1-050", {"temperature_range": ValueRange(
                minimum=-20.0, maximum=100.0, unit="degC"
            )}),
        ]
        payload = json.loads(json.dumps(induce(observations, registry).to_dict(), default=str))
        rules = [r for r in payload["rule_detail"] if r["attribute_code"] == "temperature_range"]
        assert rules
        for rule in rules:
            for value in rule["mapping"].values():
                assert value == {"minimum": -20.0, "maximum": 100.0, "unit": "degC"}

    def test_an_unknown_attribute_in_the_corpus_is_skipped_not_raised(self, registry):
        """One bad row must not void an induction run."""
        observations = [
            Observation("P-1-025", {"not_a_real_attribute": 1, "case_quantity": 24}),
            Observation("P-1-050", {"not_a_real_attribute": 2, "case_quantity": 24}),
        ]
        grammar = induce(observations, registry)
        assert not [r for r in grammar.rules if r.attribute_code == "not_a_real_attribute"]
        assert [r for r in grammar.rules if r.attribute_code == "case_quantity"]

    def test_summary_counts_shapes(self, registry, golden_observations):
        summary = induce(golden_observations, registry).summary()
        assert summary["shapes"] == {"A-N-N": 9, "NA-N": 4, "NA-NA": 2}
        assert summary["observations"] == 15
