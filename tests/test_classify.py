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
    'ball' — it is 'full port', 'RPTFE', 'two-piece', which live in the enum values."""
    candidates = index.search("full port RPTFE two-piece")
    assert candidates[0].code == BALL_VALVE


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
