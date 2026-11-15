#!/usr/bin/env python3
"""One command to re-train after adding corpus documents and/or new skills.

    python scripts/training/retrain.py                 # auto: Docker if available, else host, else skip training
    python scripts/training/retrain.py --embeddings host|docker|skip
    python scripts/training/retrain.py --force         # adopt the new embedding model even if it scores lower
    (make retrain | .\\dev.ps1 retrain | python scripts/dev/devctl.py retrain)

Stages (stops at the first failure, prints a summary, writes training/retrain_report.json):
  1. validate   workflow.yaml + plugins load; every skill (incl. new ones) has passing fixtures
  2. index      rebuild BM25 index + vectors from rag.sources; regenerate skill docs (injected compliance)
  3. coverage   list authoritative policy sections with NO held-out question (add them to natural_questions.yaml)
  4. datasets   embedding pairs (new chunks + new skills' queries) and the narrative-model SFT dataset
  5. train      fine-tune the embedding model (previous model archived first)
  6. evaluate   held-out embedding eval + full scenario evaluation (with the current default retrieval mode)
  7. gate       keep the new model only if hybrid recall@5 >= previous and non-authoritative-top1 == 0
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIN = os.name == "nt"
PY = str(ROOT / ".venv" / ("Scripts/python.exe" if WIN else "bin/python"))
EMB = ROOT / "training" / "embeddings"
MODEL, ARCHIVE = ROOT / "models" / "embedder", EMB / "archive"
REPORT = ROOT / "training" / "retrain_report.json"
summary: list[tuple[str, str, str]] = []
STATE = {"archived": None, "adopted": False}
HINTS = {
    "1 skill fixtures": "a skill's fixtures fail or are missing - run `finagent skills test <name>` and fix the skill or its fixtures",
    "6 search accuracy gate": "the configured search is less accurate than plain keyword search on at least one measure - see "
                              "training/embeddings/accuracy_gate.json; add held-out questions / skill queries or keep the previous model",
    "6 scenario evaluation": "a scenario's expected outcome changed - usually a new or changed control. Run `finagent eval` to see which; "
                             "either update the case data in scripts/generate_mock_data.py (then `make seed`) or confirm the new expected "
                             "outcome there, then re-run retrain",
}


def restore_previous_model():
    """Never leave a model in place that has not passed the adoption gate."""
    arch = STATE["archived"]
    if arch and not STATE["adopted"] and arch.exists():
        shutil.rmtree(MODEL, ignore_errors=True)
        shutil.move(str(arch), str(MODEL))
        if (MODEL / "eval_report.json").exists():
            shutil.copy(MODEL / "eval_report.json", EMB / "eval_report.json")
        print(f"previous embedding model restored (from {arch.relative_to(ROOT)})")


def step(name: str, cmd: list[str], env: dict | None = None, capture: bool = False) -> str:
    print(f"\n=== {name} ===\n+ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, text=True,
                       capture_output=capture)
    dur = f"{time.time() - t0:.0f}s"
    if r.returncode != 0:
        if capture:
            print(r.stdout[-3000:], r.stderr[-3000:])
        summary.append((name, "FAILED", dur))
        hint = next((h for k, h in HINTS.items() if name.startswith(k)), None)
        if hint:
            print(f"\nWHAT TO DO: {hint}")
        finish(ok=False)
    summary.append((name, "ok", dur))
    return r.stdout if capture else ""


def finish(ok: bool, extra: dict | None = None):
    if not ok:
        restore_previous_model()
    print("\n=== retrain summary ===")
    for n, st, d in summary:
        print(f"  {st:<7} {n:<48} {d}")
    data = {"finished_at": datetime.now().isoformat(timespec="seconds"), "ok": ok,
            "steps": [{"step": n, "status": st, "duration": d} for n, st, d in summary], **(extra or {})}
    REPORT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"\nreport: {REPORT.relative_to(ROOT)}")
    sys.exit(0 if ok else 1)


def coverage_gaps() -> list[str]:
    sys.path.insert(0, str(ROOT / "scripts" / "training"))
    import generate_embedding_pairs as gp
    covered = {k for q in gp.natural_questions() for k in (q.get("relevant") or [])}
    return sorted({gp.key(c["chunk_id"]) for c in gp.load_index().chunks if c["trust"] == "authoritative"} - covered)


def pick_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    if shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        return "docker"
    if subprocess.run([PY, "-c", "import sentence_transformers"], capture_output=True).returncode == 0:
        return "host"
    return "skip"


def hybrid_r5(report: dict, key: str) -> float | None:
    m = (report.get(key) or {}).get("hybrid") or (report.get(key) or {}).get("dense")
    return None if not m else m["recall@5"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--embeddings", choices=["auto", "docker", "host", "skip"], default="auto")
    ap.add_argument("--base-model", default=os.environ.get("BASE_MODEL", "BAAI/bge-small-en-v1.5"))
    ap.add_argument("--epochs", default=os.environ.get("EPOCHS", "3"))
    ap.add_argument("--force", action="store_true", help="adopt the new model even if it scores lower than the previous one")
    a = ap.parse_args()
    if not Path(PY).exists():
        sys.exit("virtual environment missing - run setup first (make setup / .\\dev.ps1 setup)")

    # 1. validate ---------------------------------------------------------------------------------
    step("1 validate workflow + plugins", [PY, "-m", "finagent.cli", "workflow", "validate"])
    step("1 skill fixtures (every skill incl. new ones)", [PY, "-m", "pytest", "-q", "tests/skills", "tests/unit/test_workflow_config.py"])
    # 2. index ------------------------------------------------------------------------------------
    (ROOT / "data" / "index" / "bm25_index.json").unlink(missing_ok=True)
    step("2 rebuild policy index + vectors", [PY, "-m", "finagent.cli", "ingest"], capture=True)
    step("2 regenerate skill docs (inject compliance)", [PY, "scripts/skills/build_skill_docs.py"], capture=True)
    # 3. coverage ---------------------------------------------------------------------------------
    gaps = coverage_gaps()
    summary.append(("3 held-out question coverage", "warn" if gaps else "ok", "-"))
    if gaps:
        print(f"\n=== 3 coverage ===\n{len(gaps)} authoritative section(s) have no held-out question in "
              f"training/embeddings/natural_questions.yaml (add 1-2 realistic questions each for a fair eval):")
        for g in gaps:
            print(f"   - {g}")
    # 4. datasets ---------------------------------------------------------------------------------
    step("4 embedding training pairs", [PY, "scripts/training/generate_embedding_pairs.py"], capture=True)
    step("4 narrative-model SFT dataset", [PY, "scripts/training/generate_dataset.py"], capture=True)
    # 5 + 6. train + evaluate embeddings ------------------------------------------------------------
    mode = pick_mode(a.embeddings)
    prev_report = json.loads((EMB / "eval_report.json").read_text()) if (EMB / "eval_report.json").exists() else {}
    prev_r5 = hybrid_r5(prev_report, "st:models/embedder")
    archived = None
    if mode == "skip":
        summary.append(("5 train embedding model", "skipped", "-"))
        print("\n(embedding training skipped" + (" as requested)" if a.embeddings == "skip" else
              ": no Docker and no sentence-transformers on the host - install Docker or pip install 'finagent[embeddings]')"))
    else:
        if MODEL.exists():
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            archived = STATE["archived"] = ARCHIVE / f"model-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.move(str(MODEL), str(archived))
            if (EMB / "eval_report.json").exists():
                shutil.copy(EMB / "eval_report.json", archived / "eval_report.json")
        env = {"BASE_MODEL": a.base_model, "EPOCHS": str(a.epochs)}
        if mode == "docker":
            step("5 build training image (picks up new corpus/skills)", ["docker", "compose", "--profile", "train", "build", "embed-train"], env, capture=True)
            step("5+6 train + evaluate embeddings (Docker)", ["docker", "compose", "--profile", "train", "run", "--rm", "embed-train"], env)
        else:
            step("5 train embedding model (host)", [PY, "scripts/training/train_embeddings.py", "--base", a.base_model, "--epochs", str(a.epochs)])
            step("6 evaluate embeddings (held-out)", [PY, "scripts/training/eval_embeddings.py", "--embedders", "hash", "st:models/embedder"])
    # 6. scenario regression -------------------------------------------------------------------------
    out = step("6 scenario evaluation (all cases)", [PY, "-m", "finagent.cli", "eval"], capture=True)
    print("\n".join(out.splitlines()[:3]))
    # search must not lose accuracy versus keyword search (curated recall, held-out R@1/R@5/MRR, no untrusted doc first)
    step("6 search accuracy gate (configured retrieval vs BM25)", [PY, "scripts/training/accuracy_gate.py"])
    # 7. adoption gate ------------------------------------------------------------------------------
    extra = {"embeddings_mode": mode, "coverage_gaps": gaps}
    if mode != "skip":
        new = json.loads((EMB / "eval_report.json").read_text())
        new_r5, bm25 = hybrid_r5(new, "st:models/embedder"), hybrid_r5(new, "lexical (BM25)")
        nat = ((new.get("st:models/embedder") or {}).get("hybrid") or {}).get("non_auth_top1", 0)
        better = new_r5 is not None and (prev_r5 is None or new_r5 >= prev_r5) and nat == 0
        extra.update({"new_hybrid_recall@5": new_r5, "previous_hybrid_recall@5": prev_r5, "bm25_recall@5": bm25,
                      "non_auth_top1": nat})
        if better or a.force:
            summary.append(("7 adoption gate", "ok", f"R@5 {prev_r5} -> {new_r5} (BM25 {bm25})"))
            extra["adopted"] = STATE["adopted"] = True
            if archived:
                print(f"\nnew model adopted; previous archived at {archived.relative_to(ROOT)}")
        else:
            summary.append(("7 adoption gate", "REVERTED", f"R@5 {prev_r5} -> {new_r5}"))
            extra["adopted"] = False
            shutil.rmtree(MODEL, ignore_errors=True)
            if archived:
                shutil.move(str(archived), str(MODEL))
                if (MODEL / "eval_report.json").exists():
                    shutil.copy(MODEL / "eval_report.json", EMB / "eval_report.json")
            print("\nnew model scored lower than the previous one - previous model restored (use --force to adopt anyway)")
        print("\nTo serve with the trained embedder: set rag.mode=hybrid, rag.fusion.vector_weight=4, "
              "rag.vector.embedder=sentence_transformers (see docs/local_llm_training.md section 6)")
    finish(ok=True, extra=extra)


if __name__ == "__main__":
    main()
