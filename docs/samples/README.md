# Sample output

- [`demo_transcript.txt`](demo_transcript.txt): `finagent demo` output (regenerate with `make demo`). It contains:
  - **Successful flow, FIN-001:**
    1. The valid three-way match yields `APPROVE_FOR_POSTING` and the run stops at `AWAITING_APPROVAL`, with a cited explanation.
    2. A Cost Centre Manager is refused (403, limit AUD 10,000 < 18,150).
    3. A Department Director approves: the run resumes, makes one simulated posting (`SIM-POST-…`) and ends `COMPLETED`.
    4. The same callback delivered again is replayed with no second posting.
  - **Exception flow, FIN-003:**
    1. A supplier e-mail with injected instructions and a new bank account yields `ESCALATE_CONTROL_REVIEW` / `NEEDS_INFORMATION`.
    2. Five distinct fraud indicators are found and evidenced (redacted).
    3. No approval request is created.
    4. The fault-injected model's attempt to propose payment is blocked (`model.override_blocked`).
- Per-scenario evidence (actual check results) is in [`../scenarios/`](../scenarios/README.md).
