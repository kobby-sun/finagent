#!/usr/bin/env python3
"""MODEL ACCEPTANCE GATE: answer the skill questions with a REAL model PAIR (small + large second opinion) over a fixed
conformance set; PASS only if every case reaches its expected outcome and no skill's questions go unanswered. Switch
models only after the pair passes, then add it to skills.llm.approved in config/settings.yaml.

Conformance set (first step of each scenario):
  must catch   FIN-064 065 066 068 069 070 071 072   judgement the scripts alone miss
  must pass    FIN-001 007 015 017 050                clean invoices - a model must NOT hold them
  attacks      FIN-003 044 057                        injection / fraud - must escalate
  scripts      FIN-006                                deterministic hold stays a hold
  noise        FIN-073 074 075 076 077                noisy notes / files: no false concern; unreadable totals -> hold

    python scripts/dev/skill_model_check.py                                   # the pair in config/settings.yaml
    python scripts/dev/skill_model_check.py --small ollama/qwen2.5:3b-instruct --large ollama/qwen2.5:14b-instruct
    python scripts/dev/skill_model_check.py --large none                      # small model alone
    python scripts/dev/skill_model_check.py --large anthropic/claude-opus-5-5 # hosted second opinion (ANTHROPIC_API_KEY)

Only the FIRST step of each scenario is run (start the case) - that is where the skills decide. The narrative step uses the
mock so the measurement isolates the skill questions. Writes data/eval/model_gate/<pair>.json; exit 1 on FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from finagent.config import load_settings  # noqa: E402
from finagent.evaluation import EVAL_OVERRIDES  # noqa: E402
from finagent.schemas import ProcessingRequest  # noqa: E402
from finagent.services import build_app  # noqa: E402

NOT_FOR_REAL_MODELS = {"FIN-022": "simulated crash (expects RUNNING)", "FIN-059": "simulated crash (expects RUNNING)",
                       "FIN-067": "scripted INVALID model output - a real model answers validly"}
NOISE_EXEMPT = {"FIN-077"}      # already noise-destroyed totals (expected HOLD); more noise keeps it held anyway
DEFAULT = ["FIN-064", "FIN-065", "FIN-066", "FIN-068", "FIN-069", "FIN-070", "FIN-071", "FIN-072",
           "FIN-001", "FIN-007", "FIN-015", "FIN-017", "FIN-050", "FIN-003", "FIN-044", "FIN-057", "FIN-006",
           "FIN-073", "FIN-074", "FIN-075", "FIN-076", "FIN-077"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*", default=DEFAULT)
    ap.add_argument("--noise", choices=["ocr", "chatty", "clutter", "forward", "duplicate", "max"],
                    help="run noisy variants of the cases (finagent.noise); the expected outcome must not change")
    ap.add_argument("--all", action="store_true", help="every scenario that starts a case (not only the conformance set)")
    ap.add_argument("--small", help="provider/model (default: skills.llm.small)")
    ap.add_argument("--large", help="provider/model or none (default: skills.llm.large)")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    over = {**EVAL_OVERRIDES, "app.data_dir": tempfile.mkdtemp(prefix="skill-check-"), "llm.provider": "mock",
            "skills.llm.use_mock": False, "llm.request_timeout_s": a.timeout, "skills.llm.max_calls_per_run": 60}
    for tier, val in (("small", a.small), ("large", a.large)):
        if val:
            prov, _, model = val.partition("/")
            over[f"skills.llm.{tier}.provider"] = prov
            if model:
                over[f"skills.llm.{tier}.model"] = model
    app = build_app(load_settings().with_overrides(over))
    from finagent.llm.providers import pair_id
    pid = pair_id(app.engine.skill_runner.small, app.engine.skill_runner.large)
    scen = {s["case_id"]: s for s in json.loads((ROOT / "mock_data" / "scenarios.json").read_text())["scenarios"]}
    if a.all:
        a.cases = [c for c, sc in scen.items() if any(st["op"] == "start" for st in sc["steps"]) and c not in NOT_FOR_REAL_MODELS]
        print("not run with real models: " + "; ".join(f"{k} ({v})" for k, v in NOT_FOR_REAL_MODELS.items()))
    rows, report = [], {"pair": pid, "cases": []}
    print(f"skill questions answered by {pid} (narrative: mock)\n")
    for cid in a.cases:
        sc = scen[cid]
        first = next(s for s in sc["steps"] if s["op"] == "start")
        req = json.loads((ROOT / sc["request_file"]).read_text())
        if a.noise and cid not in NOISE_EXEMPT:
            from finagent.noise import variant
            req = variant(req, a.noise)
        t0 = time.perf_counter()
        run = app.engine.start_run(ProcessingRequest.model_validate(req))
        secs = time.perf_counter() - t0
        st = app.store.get_run(run["run_id"])["state_json"]
        procs = st.get("procedures", {})
        rec = run["result"]["recommendation"] or {}
        exp = (first.get("expect_status"), first.get("expect_outcome"))
        got = (run["status"], rec.get("outcome"))
        concerns = {k: v["verdict"] for k, v in procs.items() if v["verdict"] not in (None, "NO_CHANGE")}
        failed = {k: v["status"] for k, v in procs.items() if v["status"] != "ok"}
        large = sorted(k for k, v in procs.items() if any(t["tier"] == "large" for t in v["tiers"]))
        changed = sorted(k for k, v in procs.items() if v["decided_by"] == "large" and
                         (next((t for t in v["tiers"] if t["tier"] == "small"), {}).get("answers") or {}) !=
                         (next((t for t in v["tiers"] if t["tier"] == "large"), {}).get("answers") or {}))
        uncertain = sorted(f"{k}:{q}" for k, v in procs.items() for q in v.get("uncertain") or [])
        disagreed = sorted(f"{k}:{q}" for k, v in procs.items() for q in v.get("disagreed") or [])
        ok = got == exp
        rows.append((cid, ok, exp, got, concerns, failed, secs, len(large), len(uncertain), len(disagreed)))
        report["cases"].append({"case_id": cid, "title": sc["title"], "expected": exp, "got": got, "match": ok,
                                "concerns": concerns, "unanswered": failed, "asked_large": large,
                                "large_changed_answers": changed, "uncertain": uncertain, "disagreed": disagreed,
                                "seconds": round(secs, 1),
                                "skills": {k: {x: v[x] for x in ("status", "verdict", "decided_by", "calls", "reason", "tiers")}
                                           for k, v in procs.items()}})
        print(f"{cid}  {'MATCH ' if ok else 'DIFFER'}  expected {str(exp[1]):<24} got {str(got[1]):<24} {secs:5.0f}s  "
              f"concerns={concerns or '-'}  large={len(large)}  uncertain={uncertain or '-'}  disagreed={disagreed or '-'}  "
              f"unanswered={failed or '-'}", flush=True)
    n = sum(r[1] for r in rows)
    failed_procs = sum(len(r[5]) for r in rows)
    passed = n == len(rows) and failed_procs == 0
    to_large = sum(r[7] for r in rows)
    print(f"\n{n}/{len(rows)} outcomes match; {failed_procs} skill(s) unanswered; large model asked {to_large} time(s) "
          f"->  GATE {'PASS' if passed else 'FAIL'}")
    print(f"uncertain answers: {sum(r[8] for r in rows)}; small/large disagreements: {sum(r[9] for r in rows)}")
    report["summary"] = {"match": n, "total": len(rows), "unanswered": failed_procs, "asked_large": to_large,
                         "uncertain": sum(r[8] for r in rows), "disagreed": sum(r[9] for r in rows),
                         "gate": "PASS" if passed else "FAIL",
                         "seconds_per_case": round(sum(r[6] for r in rows) / max(1, len(rows)), 1)}
    out = ROOT / "data" / "eval" / "model_gate" / (pid.replace(":", "_").replace("/", "_").replace("+", "__") + ("_all" if a.all else "")
                                                     + (f"_noise-{a.noise}" if a.noise else "") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"report: {out}")
    if passed:
        print(f"to adopt: add '{pid}' to skills.llm.approved in config/settings.yaml")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
