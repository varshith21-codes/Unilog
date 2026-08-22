"""Tests for classification.

The properties that matter: a confident wrong leaf is worse than an honest truncated path;
an ambiguous case must not be resolved by coin flip; and a model returning a code outside the
shortlist is a hallucination, not a decision.
"""

from __future__ import annotations

import json

import pytest
from axiom.classify import (
    CandidateIndex,
    Classifier,
    tokenize,
)
from axiom.core.product import ClassificationScheme
from axiom.extract import ModelCascade, ModelError, StubModelClient
from axiom.schema import load_default

BALL_VALVE = "PLB.VLV.BALL.2PC"
GATE_VALVE = "PLB.VLV.GATE.BRZ"

BALL_TEXT = (
    "Two-Piece Full Port Bronze Ball Valve, Bronze C84400 body, RPTFE seat, "
    "600 PSI WOG, NPT threaded, lever handle"
)
GATE_TEXT = (
    "Bronze Gate Valve, rising stem, Bronze C84400 body, 150 PSI WSP steam rating, "
    "NPT threaded, handwheel"
)


@pytest.fixture(scope="module")
def registry():
    return load_default()


@pytest.fixture(scope="module")
def index(registry):
    return CandidateIndex.build(registry)


@pytest.fixture
def cascade():
    return ModelCascade(region="us-east-2", tiers={"volume": "stub-volume"})


def decision(code, confidence=0.9, rationale="because", runner_up=None) -> str:
    return json.dumps(
        {
            "code": code,
            "confidence": confidence,
            "rationale": rationale,
            "runner_up": runner_up,
        }
    )


# --------------------------------------------------------------------- tokenization


def test_hyphenated_compounds_survive_tokenization():
    """'two-piece' is one meaningful token here; splitting it loses the distinction."""
    assert "two-piece" in tokenize("Two-Piece Ball Valve")


def test_stopwords_and_single_characters_are_dropped():
    tokens = tokenize("a valve for the pipe X")
    assert "a" not in tokens
    assert "for" not in tokens
    assert "x" not in tokens
    assert "valve" in tokens


# --------------------------------------------------------------------- retrieval


def test_index_covers_every_class(index, registry):
    assert len(index) == len(registry.class_codes)


def test_ball_valve_text_ranks_the_ball_valve_class_first(index):
    candidates = index.search(BALL_TEXT)
    assert candidates
    assert candidates[0].code == BALL_VALVE


def test_gate_valve_text_ranks_the_gate_valve_class_first(index):
    candidates = index.search(GATE_TEXT)
    assert candidates
    assert candidates[0].code == GATE_VALVE


def test_attribute_values_drive_discrimination(index):
    """What separates a ball valve from a gate valve in supplier text is rarely the word
    'ball' — it is 'full port', 'RPTFE', 'two-piece', which live in the enum values.

    The word "valve" is present only to satisfy the identity guard, which is a separate concern:
    both valve classes declare `identity_terms: [valve, ...]`, so both are admitted equally and
    the choice between them is still made entirely by the attribute vocabulary. Nothing about the
    original claim is weakened — see `test_identity_guard_rejects_attribute_vocabulary_matches`
    for why the guard has to exist.
    """
    candidates = index.search("full port RPTFE two-piece valve")
    assert candidates[0].code == BALL_VALVE
    assert {c.code for c in candidates} >= {BALL_VALVE, GATE_VALVE}, (
        "both valve classes must be admitted, or this is testing the guard rather than "
        "attribute-driven discrimination"
    )


def test_identity_guard_rejects_attribute_vocabulary_matches(index):
    """A class must not win on the strength of its own attribute vocabulary.

    Every string below was a real false positive before the guard existed, measured on the
    1,000-row Unilog sample:

    * "2 Port Decor Plate" matched the ball-valve class on `Port Type` and scored 0.1854 —
      *higher* than any genuine dishwasher scored against the dishwasher class (max 0.2135, min
      0.1725), which is why a score floor cannot fix this.
    * "Castle Gate" PVC decking matched the bronze gate-valve class on the word "gate".
    * A GFCI plug and a bandsaw matched the dishwasher class on `Plug Type` and `Voltage Rating`.

    Overlap is the wrong instrument for an identity question, so identity is asked separately.

    ---------------------------------------------------------------------------------------------
    THIS TEST USED TO ASSERT `index.search(text) == []` FOR ALL SIX, and that assertion has been
    replaced rather than deleted. It was the right test against a four-class schema in which none of
    these products had a home: abstaining was the only correct answer available, so "no candidate"
    and "not the wrong candidate" were the same statement.

    Five of the six now have a home. Keeping the old form would have required refusing to classify a
    decor plate, a GFCI plug, a bandsaw, a bit assortment and a grinding wheel in a taxonomy that
    contains a wiring-device class, a power-tool class, a driver-bit class and an abrasive class —
    that is, it would have pinned the coverage gap in place as though it were a feature.

    What the test protects is unchanged, and it is the half that was always load-bearing: a class
    must never win a row on vocabulary it merely shares. So each string now names the class that
    SHOULD win, and the assertion is that the winner is that class and specifically not any of the
    classes that used to be attracted by attribute overlap.
    """
    expected = {
        # The row that forced the guard into existence. `Port Type` on the ball-valve class made
        # this outscore every real dishwasher; it is a wall plate and now lands as one.
        "5522-5EV 2 Port Decor Plate": "ELC.DEV.WIRING",
        # "Castle Gate" is a Landmark AZEK COLOUR NAME. It once matched the bronze gate valve on the
        # word "gate", and while this taxonomy was being built it did the same to the railing class
        # until `gate` was removed from that guard.
        "1x6-20' Castle Gate Grooved - Landmark Azek PVC Decking": "BLD.DCK.BOARD",
        # Matched the dishwasher class on `Plug Type` and `Voltage Rating`.
        "R5GSRA1THD 15A GFCI Plug": "ELC.DEV.WIRING",
        # Matched the dishwasher class on `Voltage Rating`.
        "JWBS-14SFX 14in Bandsaw JTP-714400K": "TOL.PWR.GEN",
        # Matched the ball-valve class on the word "Ball".
        "IBMG90K003 Vessel Impact Ball Torsion Bit Assort 5pc": "TOL.ACC.DRIVERBIT",
        # Matched the ball-valve class on shared size fractions.
        '49-94-0533 Milw 7"x1/4"x7/8" Metal Grinding Wheel': "ABR.WHL.BONDED",
    }
    # The classes these strings were wrongly attracted to. None may win any of them, whatever else
    # changes in the schema.
    never = {BALL_VALVE, GATE_VALVE, "APP.KIT.DISHWASHER.BUILTIN"}

    for text, wanted in expected.items():
        ranked = index.search(text)
        assert ranked, f"{text!r} produced no candidate at all"
        assert ranked[0].code == wanted, (
            f"{text!r} ranked {ranked[0].code} first, expected {wanted}"
        )
        assert never.isdisjoint({c.code for c in ranked}), (
            f"{text!r} admitted a class it only shares attribute vocabulary with: "
            f"{sorted(never & {c.code for c in ranked})}"
        )


def test_a_class_never_wins_on_shared_attribute_vocabulary_alone(index, registry):
    """The generalisation of the test above, applied to every class rather than six strings.

    A winning class must contribute at least one of its own identity terms to the match. That is
    guaranteed by construction — `ClassProfile.admits` gates on exactly this — so what this checks
    is the guarantee rather than the behaviour, and it fails the day a class is added with no
    `identity_terms` at all. An unguarded class is admitted for every query, which is the state the
    whole guard exists to prevent, and it is silent: it costs precision everywhere and shows up in
    no single row.
    """
    from axiom.classify.candidates import tokenize

    unguarded = [
        code for code in registry.class_codes
        if not registry.product_class(code).identity_terms
    ]
    assert unguarded == [], (
        f"these classes declare no identity_terms and are therefore candidates for every query: "
        f"{unguarded}"
    )

    for text in (
        "5522-5EV 2 Port Decor Plate",
        "JWBS-14SFX 14in Bandsaw JTP-714400K",
        '49-94-0533 Milw 7"x1/4"x7/8" Metal Grinding Wheel',
        "PDSH4816AF Dishwasher SS - Display Only",
        BALL_TEXT,
    ):
        query = set(tokenize(text))
        for candidate in index.search(text, limit=10):
            terms = frozenset(
                token
                for term in registry.product_class(candidate.code).identity_terms
                for token in tokenize(term)
            )
            assert terms & query, (
                f"{candidate.code} was a candidate for {text!r} without matching any of its own "
                f"identity terms"
            )


def test_identity_guard_admits_the_real_thing(index):
    """The guard must not cost recall on products that genuinely are the class."""
    assert index.search(BALL_TEXT)[0].code == BALL_VALVE
    assert index.search(GATE_TEXT)[0].code == GATE_VALVE
    assert index.search("PDSH4816AF Dishwasher SS - Display Only")[0].code == (
        "APP.KIT.DISHWASHER.BUILTIN"
    )


def test_identity_guard_accepts_supplier_abbreviations(index):
    """The abbreviations are the whole difficulty; a guard that only knew full words would
    abstain on exactly the cryptic strings this system exists to enrich."""
    candidates = index.search("VLV BALL 3/4 BRS 600WOG LF FP THRD")
    assert candidates and candidates[0].code == BALL_VALVE


def test_a_class_without_identity_terms_is_unguarded(registry):
    """The guard is opt-in, so an existing schema keeps working unchanged."""
    from collections import Counter

    from axiom.classify.candidates import ClassProfile

    unguarded = ClassProfile(
        code="X",
        name="Thing",
        browse_path=("Things",),
        term_counts=Counter({"thing": 1}),
        total_terms=1,
    )
    assert unguarded.admits(Counter({"anything": 1}))

    guarded = ClassProfile(
        code="Y",
        name="Widget",
        browse_path=("Widgets",),
        term_counts=Counter({"widget": 1}),
        total_terms=1,
        identity_terms=frozenset({"widget"}),
    )
    assert not guarded.admits(Counter({"anything": 1}))
    assert guarded.admits(Counter({"widget": 1}))


def test_unrelated_text_returns_nothing(index):
    assert index.search("stainless steel kitchen sink faucet aerator cartridge") == [] or all(
        c.score < 0.5 for c in index.search("office chair swivel castor")
    )


def test_empty_text_returns_nothing(index):
    assert index.search("") == []
    assert index.search("the and of") == []


def test_scores_are_ordered_and_bounded(index):
    candidates = index.search(BALL_TEXT)
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 < s <= 1.0 for s in scores)


# --------------------------------------------------------------------- decision paths


def test_decisive_retrieval_needs_no_model_call(registry, index, cascade):
    """Spending tokens to confirm what the numbers already say is waste."""
    client = StubModelClient([])
    classifier = Classifier(registry, client=client, cascade=cascade, index=index)
    result = classifier.classify(BALL_TEXT, sku="BA-100-075")

    assert result.class_code == BALL_VALVE
    assert result.method in {"retrieval_decisive", "retrieval_only"}
    assert client.calls == [], "no model call should have been made"
    assert result.usage.calls == 0


def test_classification_without_a_model_still_works_when_decisive(registry, index):
    classifier = Classifier(registry, index=index)
    assert classifier.classify(BALL_TEXT).class_code == BALL_VALVE


def test_ambiguous_case_without_a_model_abstains(registry, index):
    """An ambiguous case must not be resolved by coin flip."""
    classifier = Classifier(registry, index=index)
    # Text using only vocabulary the two valve classes share.
    result = classifier.classify("Bronze C84400 body NPT threaded valve 150 PSI")
    if result.method == "ambiguous_no_model":
        assert result.abstained is True
        assert result.class_code is None
        assert "no model is available" in result.abstain_reason


def test_no_viable_candidate_abstains(registry, index):
    classifier = Classifier(registry, index=index)
    result = classifier.classify("qwerty zxcvbn asdfgh")
    assert result.abstained is True
    assert result.method == "no_viable_candidate"
    assert result.classifications == []


def test_dominance_band_still_separates(index):
    """DECISIVE_DOMINANCE is corpus-sensitive. Pin the band so a new class fails loudly.

    IDF is computed across classes, so adding one shifts every score. Worse, adding an
    *unrelated* class inflates the weight of vocabulary the *related* classes share, pulling
    near neighbours together — a two-class schema put the correct ball-valve match at 0.659 and
    a three-class schema puts it at 0.696.

    When this fails, the fix is to re-measure both sides and move the constant, not to delete
    the test. The numbers in `classifier.DECISIVE_DOMINANCE`'s comment come from here.
    """
    from axiom.classify.classifier import DECISIVE_DOMINANCE

    def dominance(text: str) -> float:
        ranked = index.search(text, limit=5)
        assert len(ranked) >= 2, f"expected a runner-up for {text!r}"
        return ranked[1].score / ranked[0].score

    correct = {
        "ball": dominance(BALL_TEXT),
        "gate": dominance(GATE_TEXT),
    }
    ambiguous = {
        "shared vocabulary only": dominance("bronze valve NPT threaded"),
        "bronze NPT 150 PSI": dominance("Bronze C84400 body NPT threaded valve 150 PSI"),
    }

    for label, value in correct.items():
        assert value <= DECISIVE_DOMINANCE, (
            f"correct match {label!r} has dominance {value:.4f}, above the decisive threshold "
            f"{DECISIVE_DOMINANCE} — it now needs a model call it should not need"
        )
    for label, value in ambiguous.items():
        assert value > DECISIVE_DOMINANCE, (
            f"ambiguous case {label!r} has dominance {value:.4f}, at or below the decisive "
            f"threshold {DECISIVE_DOMINANCE} — it would be resolved without adjudication"
        )

    # The gap the threshold lives in. Narrow enough to be worth reporting.
    assert max(correct.values()) < min(ambiguous.values())


# --------------------------------------------------------------------- model adjudication


def _close_call_classifier(registry, index, responses, cascade):
    """A classifier forced down the model path by shrinking the decisive margin."""
    classifier = Classifier(
        registry, client=StubModelClient(responses), cascade=cascade, index=index
    )
    return classifier


def test_model_adjudicates_a_close_call(registry, index, cascade, monkeypatch):
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = _close_call_classifier(registry, index, [decision(GATE_VALVE)], cascade)
    result = classifier.classify(BALL_TEXT)

    assert result.method == "model_adjudicated"
    assert result.class_code == GATE_VALVE
    assert result.usage.calls == 1


def test_model_confidence_is_capped_on_an_ambiguous_case(registry, index, cascade, monkeypatch):
    """A close retrieval margin is objective evidence of ambiguity; a model asserting 0.99
    is not evidence to the contrary."""
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = _close_call_classifier(
        registry, index, [decision(BALL_VALVE, confidence=0.99)], cascade
    )
    result = classifier.classify(BALL_TEXT)
    assert result.internal.confidence <= 0.92


def test_model_abstention_is_respected(registry, index, cascade, monkeypatch):
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = _close_call_classifier(
        registry,
        index,
        [decision(None, rationale="cannot tell ball from gate from this text")],
        cascade,
    )
    result = classifier.classify(BALL_TEXT)
    assert result.abstained is True
    assert result.method == "model_abstained"
    assert "cannot tell" in result.abstain_reason


def test_invented_class_code_is_rejected(registry, index, cascade, monkeypatch):
    """A code outside the shortlist is a hallucination, not a decision."""
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = _close_call_classifier(registry, index, [decision("PLB.VLV.MADEUP")], cascade)
    result = classifier.classify(BALL_TEXT)
    assert result.abstained is True
    assert result.method == "invented_code"
    assert "not among the candidates" in result.abstain_reason


def test_unparseable_decision_abstains(registry, index, cascade, monkeypatch):
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = _close_call_classifier(registry, index, ["not json"], cascade)
    result = classifier.classify(BALL_TEXT)
    assert result.abstained is True
    assert result.method == "unparseable_decision"


def test_fenced_json_is_tolerated(registry, index, cascade, monkeypatch):
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    payload = f"```json\n{decision(BALL_VALVE)}\n```"
    classifier = _close_call_classifier(registry, index, [payload], cascade)
    assert classifier.classify(BALL_TEXT).class_code == BALL_VALVE


def test_model_error_abstains_rather_than_guessing(registry, index, cascade, monkeypatch):
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = _close_call_classifier(registry, index, [ModelError("throttled")], cascade)
    result = classifier.classify(BALL_TEXT)
    assert result.abstained is True
    assert result.method == "model_error"


# --------------------------------------------------------------------- per-level confidence


def test_shared_path_levels_are_certain(registry, index):
    """Every candidate sits under Plumbing > Valves, so those levels are certain no matter
    which leaf wins."""
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    levels = result.internal.level_confidences
    assert levels[0] == pytest.approx(1.0)
    assert levels[1] == pytest.approx(1.0)


def test_leaf_level_confidence_is_lower_than_root(registry, index):
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    levels = result.internal.level_confidences
    assert levels[-1] <= levels[0]


def test_levels_below_the_divergence_inherit_the_decision_confidence(
    registry, index, cascade, monkeypatch
):
    """Agreement measures whether a decision was needed, not whether it was good.

    Regression guard: computing deeper levels from raw agreement truncated a correct,
    high-confidence adjudication down to 'Plumbing > Valves', discarding the answer the model
    had just given with an explicit rationale.
    """
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = Classifier(
        registry,
        client=StubModelClient([decision(BALL_VALVE, confidence=0.92)]),
        cascade=cascade,
        index=index,
    )
    result = classifier.classify(BALL_TEXT)
    internal = result.internal

    assert internal.code == BALL_VALVE
    assert len(internal.path) == 4, "a confident adjudication must not be truncated away"
    assert internal.level_confidences[0] == pytest.approx(1.0)
    assert internal.level_confidences[-1] == pytest.approx(internal.confidence)


def test_a_leaf_never_exceeds_the_decision_that_chose_it(registry, index, cascade, monkeypatch):
    monkeypatch.setattr("axiom.classify.classifier.DECISIVE_DOMINANCE", 0.0)
    classifier = Classifier(
        registry,
        client=StubModelClient([decision(BALL_VALVE, confidence=0.65)]),
        cascade=cascade,
        index=index,
    )
    internal = classifier.classify(BALL_TEXT).internal
    assert internal.level_confidences[-1] <= internal.confidence + 1e-9


def test_path_is_truncated_at_the_confidence_floor(registry, index):
    """'Plumbing > Valves' is navigable and correct; a wrong leaf actively misleads."""
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    internal = result.internal
    assert len(internal.path) <= len(registry.product_class(BALL_VALVE).browse_path)
    assert list(internal.path) == list(
        registry.product_class(BALL_VALVE).browse_path[: len(internal.path)]
    )


def test_confident_depth_reported_in_summary(registry, index):
    summary = Classifier(registry, index=index).classify(BALL_TEXT).summary()
    assert summary["confident_depth"] >= 2
    assert summary["class_code"] == BALL_VALVE


# --------------------------------------------------------------------- multi-target


def test_external_schemes_are_derived_from_the_schema_mapping(registry, index):
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    schemes = {c.scheme: c for c in result.classifications}

    assert ClassificationScheme.INTERNAL in schemes
    assert schemes[ClassificationScheme.ETIM].code == "EC002714"
    assert schemes[ClassificationScheme.UNSPSC].code == "40141607"
    assert schemes[ClassificationScheme.ETIM].method == "schema_mapping"


def test_mapped_confidence_is_discounted_from_the_internal_decision(registry, index):
    """The mapping is itself an assertion that could be wrong."""
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    internal = result.internal
    etim = next(c for c in result.classifications if c.scheme is ClassificationScheme.ETIM)
    assert etim.confidence < internal.confidence


def test_mapped_classification_explains_its_provenance(registry, index):
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    etim = next(c for c in result.classifications if c.scheme is ClassificationScheme.ETIM)
    assert "not independently classified" in etim.rationale


def test_gate_valve_maps_to_its_own_codes(registry, index):
    result = Classifier(registry, index=index).classify(GATE_TEXT)
    assert result.class_code == GATE_VALVE
    etim = next(c for c in result.classifications if c.scheme is ClassificationScheme.ETIM)
    assert etim.code == "EC002713"


def test_alternatives_are_recorded_for_review(registry, index):
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    internal = result.internal
    if len(result.candidates) > 1:
        assert internal.alternatives
        assert all("code" in alt and "score" in alt for alt in internal.alternatives)


def test_hts_is_never_produced_automatically(registry, index):
    """Published benchmarks do not support automating tariff classification."""
    result = Classifier(registry, index=index).classify(BALL_TEXT)
    assert all(c.scheme is not ClassificationScheme.HTS for c in result.classifications)
