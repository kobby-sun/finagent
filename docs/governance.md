# Policy changes: system maintenance, checked at start-up

Policy changes are **maintenance releases**, never applied while the service runs. A running service keeps the policy
text, search index and rules it started with. Everything a policy change affects is checked again **at start-up**.

The brief does not ask for live policy changes. Doing them safely needs a compiler, approvals and a background
worker. Treating a change as a reviewed release is simpler, and each change gets tested before it goes live.

## 1. How a policy change is made

| Step | What | Command |
|---|---|---|
| 1 | Edit the policy file(s) | `finance_rag_corpus/*.md` |
| 2 | Update what enforces it: the policy numbers, the controls and the skill questions, each with tests | `config/policy_parameters.yaml`, `src/finagent/skills/processors/controls.py`, `skills/<name>/` |
| 3 | Prove it | `make test eval` |
| 4 | Retrain the search model (optional here; start-up does it if you skip it) | `make retrain` |
| 5 | Record that the controls were checked against the new text | `finagent policy baseline --reviewer <id> --note "<why>"` |
| 6 | Deploy / restart | `make restart`, or a new image |

Step 5 is a deliberate human act. It rewrites `config/policy_baseline.yaml`, which holds a hash for every section and
the controls that enforce it. Only an active **Financial Controller or Chief Financial Officer** in the authority register
may run it (`policy.baseline_roles`), and a note is required. The result goes into the hash-chained audit trail:

- `policy.baseline_recorded` holds who, their role, the note, and every changed section with its old and new hash;
- a refused attempt is recorded as `policy.baseline_denied`, and nothing is written.

The startup checks never write this file.

## 2. What start-up checks

`services.build_app` runs these checks before the service takes any case:

| Check | If it fails | Code |
|---|---|---|
| **Index matches the corpus** | BM25 index and vectors are rebuilt from the files | `rag/retriever.py` (`rag.auto_reindex`) |
| **Search model trained on this corpus** (`models/embedder/trained_on.json` vs index version) | The gated retrain runs (`scripts/training/retrain.py`); the new model is kept only if it is at least as accurate. If retraining is off (`rag.retrain_on_startup`), impossible or fails, **keyword search (BM25)** is used. An unverified model is never used | `services.ensure_embedder_current` |
| **Policy text matches the verified baseline** (`config/policy_baseline.yaml`) | **Every case is held** at DECIDE (the "policy gate") until the maintenance in section 1 is done. `/healthz` shows `"policy": "maintenance_required"` | `governance/baseline.py`, `agent/graph.py` |

The policy gate is why a skipped maintenance step can't do any harm. If someone edits a policy file and restarts without
updating and re-baselining, nothing is approved:

- each held case shows the changed sections (`result.policy.unverified_sections`);
- the audit log records `policy.unverified`;
- once maintenance is done, `POST /runs/{id}/evidence` re-runs the case.

Two more checks protect cases that span a restart. A decision's cited policy is checked again before posting, and a
resumed run's cited policy is checked again before deciding. If that text changed, posting is blocked, the case is held,
and the audit log records `policy.changed_since_decision` / `policy.changed_during_run`.

## 3. Policy numbers (parameters)

Every number or list the controls enforce sits in one reviewed place:
- `config/policy_parameters.yaml` holds the values;
- `src/finagent/governance/params.py` is the catalog, giving each value's source section, unit and description.

The controls read these values through `P(ev, name)` instead of hard-coding them. `finagent policy params` and
`GET /policy/parameters` list them. A PO's own tolerance block applies only when it records a **negotiated, tighter**
tolerance (`"negotiated": true`); it can never be looser than the policy.

### Interpretations (the policy gives no number; the value was chosen when the controls were built)

These have `cite: null` and should be reviewed by a finance owner:

| Parameter | Value | Policy wording |
|---|---|---|
| `match.tax_tolerance_aud` | 0.05 | "Tax … assessed separately" (FIN-POL-002 s2) |
| `credit.tax_tolerance_aud` | 0.05 | credit-note GST check (FIN-POL-008) |
| `duplicates.fuzzy_amount_pct` | 0.005 | "probable fuzzy match" (FIN-POL-005 s2) |
| `duplicates.fuzzy_date_window_days` | 14 | as above |

Other interpretations to review:
- the fraud-indicator count includes language patterns only for some indicators;
- "business days" ignore public holidays;
- "two or more non-PO invoices in 90 days" counts the current invoice.

## 4. Operating it

| Need | Command / endpoint |
|---|---|
| Does the policy match the verified baseline? | `finagent policy status`, `make policy`, `GET /policy/status`, `/healthz` → `policy` |
| Current policy numbers | `finagent policy params`, `GET /policy/parameters` |
| Re-baseline (last maintenance step; Financial Controller / CFO; audited) | `finagent policy baseline --reviewer <id> --note "<why>"` |
| Which baseline a case used | `result.policy` (reviewed by / at, unverified sections) |

The current baseline was recorded by the build (`reviewed_by: finagent build`). A finance owner should review the
interpretations above and re-baseline with their own id.

Tests: `tests/contract/test_policy_freshness.py` covers:
- no live change while running;
- a restart with changed policy holds every case;
- re-baselining restores normal processing, and is audited;
- only an authorised reviewer with a note may re-baseline (refusals are audited);
- an approval granted before a change cannot post;
- a deletion during an interrupted run.

`tests/unit/test_search_accuracy.py` covers the search-model check at start-up.
