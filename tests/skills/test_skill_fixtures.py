"""Every skill ships with fixtures (skills/<name>/tests/fixtures.yaml); all must pass, and every skill must have them."""
import pytest

from finagent.skills.base import REGISTRY
from finagent.skills.testing import SkillHarness, load_fixtures

ALL = load_fixtures()


@pytest.fixture(scope="module")
def harness(app):
    return SkillHarness(app)


@pytest.mark.parametrize("skill,fx", ALL, ids=[f"{s}:{f['id']}" for s, f in ALL])
def test_skill_fixture(harness, skill, fx):
    r = harness.run_fixture(skill, fx)
    assert r.passed, f"{skill}/{fx['id']}: " + "; ".join(r.failures)


def test_every_skill_has_fixtures(app):
    by_skill = {}
    for s, f in ALL:
        by_skill.setdefault(s, []).append(f)
    for name, spec in REGISTRY.items():
        fx = by_skill.get(name, [])
        assert len(fx) >= 3, f"skill {name} needs >= 3 fixtures, has {len(fx)}"
        if spec.kind == "processor" and name not in {"payment_scheduling"}:
            assert any(f["expect"].get("votes") == [] for f in fx), f"{name}: needs a pass-path fixture"
            assert any(f["expect"].get("votes") or "FAIL" in (f["expect"].get("findings") or {}).values() for f in fx), \
                f"{name}: needs a fail-path fixture"


def test_every_skill_cites_existing_policy(app):
    from finagent.skills.base import resolve_compliance
    for name, spec in REGISTRY.items():
        injected = resolve_compliance(app.retriever.index.chunks, spec.compliance_refs)
        assert len(injected) >= len(spec.compliance_refs), f"{name}: compliance refs not found in corpus"
