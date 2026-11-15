import json

from finagent.llm.drafting import extract_json, validate_draft

GOOD = {"summary": "All controls passed for the case.", "proposed_outcome": "APPROVE_FOR_POSTING", "rationale": ["ok"],
        "cited_source_ids": ["FIN-POL-002@v2.4#s2"], "confidence": 0.9}


def test_extract_json_from_fenced_and_prose():
    assert extract_json("Here:\n```json\n" + json.dumps(GOOD) + "\n```")["proposed_outcome"] == "APPROVE_FOR_POSTING"
    assert extract_json("prefix " + json.dumps(GOOD) + " suffix")["confidence"] == 0.9


def test_truncated_output_rejected():
    draft, errors = validate_draft('{"summary": "x", "proposed_outcome": "APPROVE"', {"a"}, set())
    assert draft is None and errors


def test_invalid_enum_rejected():
    bad = {**GOOD, "proposed_outcome": "PAY_NOW"}
    draft, errors = validate_draft(json.dumps(bad), {"FIN-POL-002@v2.4#s2"}, set())
    assert draft is None and any("proposed_outcome" in e for e in errors)


def test_unknown_citation_rejected():
    draft, errors = validate_draft(json.dumps({**GOOD, "cited_source_ids": ["MADE-UP-1"]}), {"FIN-POL-002@v2.4#s2"}, set())
    assert draft is None and "not in allowed_source_ids" in errors[0]


def test_untrusted_citation_rejected():
    d = {**GOOD, "cited_source_ids": ["ADV-001@v1.0#s0"]}
    draft, errors = validate_draft(json.dumps(d), {"ADV-001@v1.0#s0"}, {"ADV-001@v1.0#s0"})
    assert draft is None and "superseded/untrusted" in errors[0]


def test_valid_draft_accepted():
    draft, errors = validate_draft(json.dumps(GOOD), {"FIN-POL-002@v2.4#s2"}, set())
    assert draft is not None and not errors
