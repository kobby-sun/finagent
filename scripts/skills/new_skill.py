#!/usr/bin/env python3
"""Scaffold a new control skill as a plugin (no core code changes).

    python scripts/skills/new_skill.py contractor_timesheet_check --policy FIN-POL-013 s2 --stage matching --when is_invoice
    (make new-skill NAME=contractor_timesheet_check POLICY="FIN-POL-013 s2")

Creates (the same pack contract as every built-in skill):
  plugins/skills/<name>.py               deterministic script template (pure function, policy-referenced, votes)
  skills/<name>/SKILL.md                 spec (front matter); add fixed questions only if the skill needs judgement
  skills/<name>/references/policy.md     placeholder; `finagent skills docs` injects the policy text
  skills/<name>/tests/fixtures.yaml      3 fixture stubs (pass path, fail path, boundary) - fill them in
and prints the config/workflow.yaml snippet to enable it. Then: edit, run `finagent skills test <name>`, then `make retrain`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE = '''"""Skill: {name} - {desc}

Governing policy: {refs_txt}. Registered as a plugin; enable it in config/workflow.yaml.
"""
from finagent.schemas import Outcome
from finagent.skills.base import SkillOutput, skill


@skill("{name}", "processor", "{desc}",
       compliance_refs=[{refs_py}],
       retrieval_queries=["{query}"],
       version="1.0")
def {name}(ev, prior=None) -> SkillOutput:
    out = SkillOutput("{name}")
    inv = ev.get("invoice") or {{}}
    # TODO: implement the control. Read only from `ev` (request, invoice, docs, vendor, po, history, fx, authority, now).
    #       Never call tools or the model. Record a finding for PASS and FAIL; on FAIL add an exception and a vote.
    ok = True
    if ok:
        out.finding("{name}", "{rule}", "PASS", "TODO: why it passed", [{refs_first}])
    else:
        out.finding("{name}", "{rule}", "FAIL", "TODO: what failed", [{refs_first}])
        out.exception("OTHER_CONTROL_RISK", "{rule}", "TODO expected", "TODO observed", "Accounts Payable", [{refs_first}])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "TODO: reason")
    return out
'''

FIXTURES = '''# Fixtures for skill '{name}'. Run: finagent skills test {name}
# Replace the TODO expectations; tests/skills requires >= 3 fixtures incl. a pass path and a fail path.
skill: {name}
fixtures:
- id: {short}-pass
  description: TODO - a scenario where the control passes
  case: FIN-001
  expect:
    votes: []
    findings: {{{name}: PASS}}
- id: {short}-fail
  description: TODO - a scenario (or patched variant) where the control fails
  case: FIN-001
  patch: {{}}          # e.g. {{"invoice.lines.0.amount": "999.00"}}
  expect:
    votes: [HOLD_FOR_INFORMATION]
    findings: {{{name}: FAIL}}
- id: {short}-boundary
  description: TODO - the exact threshold value
  case: FIN-001
  expect:
    votes: []
'''


SKILL_MD = """---
name: {name}
version: '1.0'
kind: processor
execution: code                # deterministic script only. For judgement, set code+llm and add evidence + questions:
description: {desc}
policy:
{policy}
references: [policy.md]
# evidence: [docs]             # what code collects for the questions (docs, request.notes, invoice.lines, invoice.non_po,
#                              # po.lines, po.receipts)
# questions:                   # fixed questions; code maps each answer to a verdict with `on`
# - id: example_question
#   ask: 'TODO: one yes/no question the script cannot answer'
#   answers: ['no', 'yes']
#   'on': {{'yes': FLAG}}        # NO_CHANGE | FLAG (risk indicator) | HOLD | ESCALATE (need on_concern)
#   finding: example finding
#   indicator: example indicator
# on_concern: {{category: OTHER_CONTROL_RISK, owner: Accounts Payable, expected: 'TODO'}}
---

# {name}

{desc}

TODO: what the script checks; if it has questions, what each answer means (table: answer -> code decides).
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="snake_case skill name")
    ap.add_argument("--policy", nargs="+", default=["FIN-POL-001", "s5"], help="pairs: DOC SECTION [DOC SECTION ...]")
    ap.add_argument("--desc", default="TODO: one-line description of the control")
    ap.add_argument("--stage", default="matching")
    ap.add_argument("--when", default="always")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]+", a.name):
        sys.exit("name must be snake_case")
    pairs = list(zip(a.policy[::2], a.policy[1::2]))
    refs_py = ", ".join(f'("{d}", "{s}")' for d, s in pairs)
    plugin = ROOT / "plugins" / "skills" / f"{a.name}.py"
    fixtures = ROOT / "skills" / a.name / "tests" / "fixtures.yaml"
    if plugin.exists() or fixtures.exists():
        sys.exit(f"{a.name} already exists")
    plugin.parent.mkdir(parents=True, exist_ok=True)
    (plugin.parent / "__init__.py").touch()
    plugin.write_text(TEMPLATE.format(name=a.name, desc=a.desc, refs_py=refs_py, refs_first=f'("{pairs[0][0]}", "{pairs[0][1]}")',
                                      refs_txt=", ".join(f"{d} {s}" for d, s in pairs), rule=f"{pairs[0][0]} {pairs[0][1]}",
                                      query=a.desc.lower().replace("todo: ", "")))
    fixtures.parent.mkdir(parents=True, exist_ok=True)
    fixtures.write_text(FIXTURES.format(name=a.name, short=a.name.split("_")[0]))
    pack = ROOT / "skills" / a.name
    (pack / "references").mkdir(exist_ok=True)
    (pack / "references" / "policy.md").write_text("# Policy sections (run `finagent skills docs` to inject them)\n")
    policy = "\n".join(f"- {d}#{s}" for d, s in pairs)
    (pack / "SKILL.md").write_text(SKILL_MD.format(name=a.name, desc=a.desc, policy=policy))
    print(f"created {plugin.relative_to(ROOT)}\ncreated {fixtures.relative_to(ROOT)}\ncreated {(pack / 'SKILL.md').relative_to(ROOT)}\n")
    print("Add to config/workflow.yaml:\n")
    print(f'plugins: ["plugins.skills.{a.name}"]        # append to the existing list')
    print(f"  - name: {a.stage}\n    skills:\n      - skill: {a.name}\n        when: {a.when}\n")
    print(f"Then: edit both files -> finagent workflow validate -> finagent skills test {a.name} -> make retrain")


if __name__ == "__main__":
    main()
