"""Policy baseline check: policy changes are SYSTEM MAINTENANCE, never applied live.

``config/policy_baseline.yaml`` records the text (hash) of every policy section the code-based controls were verified
against. At start-up the index is built from the corpus; this module compares the two:

    every section matches the baseline  -> normal operation
    any section changed / added / removed -> every case is HELD at DECIDE ("policy changed - maintenance required"),
                                             /healthz reports it, nothing is posted

A policy change is made as a maintenance release (docs/governance.md):
    1. edit the policy file(s) in finance_rag_corpus/
    2. update the controls / skills / config/policy_parameters.yaml to match, with tests
    3. run the tests and the eval (make test eval)
    4. record the reviewed texts:  finagent policy baseline --reviewer <id> --note "<why>"
    5. deploy / restart
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

import yaml

from ..skills.base import REGISTRY
from .params import CATALOG, load_baseline


def norm_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


def section_key(chunk: dict) -> str:
    return f"{chunk['document_id']}#{chunk['chunk_id'].split('#')[1].split('.')[0]}"


class PolicyBaseline:
    def __init__(self, settings, retriever):
        self.settings, self.retriever = settings, retriever
        self.baseline_path = settings.root / settings.get("policy.baseline_file", "config/policy_baseline.yaml")
        self.params_path = settings.root / settings.get("policy.parameters_file", "config/policy_parameters.yaml")
        self.params = load_baseline(self.params_path)      # the policy numbers the controls enforce
        self.baseline = self._load()

    def _load(self) -> dict:
        if not self.baseline_path.exists():
            return {"sections": {}}
        return yaml.safe_load(self.baseline_path.read_text(encoding="utf-8")) or {"sections": {}}

    def current_sections(self) -> dict[str, dict]:
        """Authoritative sections in the index: {key: {document_id, version, title, hash}}."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for c in self.retriever.index.chunks:
            if c.get("trust") == "authoritative":
                groups[section_key(c)].append(c)
        out = {}
        for k, cs in groups.items():
            cs = sorted(cs, key=lambda c: c["chunk_id"])
            text = "\n".join(c["text"] for c in cs)
            out[k] = {"document_id": cs[0]["document_id"], "version": cs[0].get("version"), "title": cs[0].get("section"),
                      "hash": norm_hash(text)}
        return out

    def changes(self) -> list[dict]:
        """Sections whose text differs from the verified baseline: [{section, change: changed|new|removed}]."""
        cur, base = self.current_sections(), self.baseline.get("sections") or {}
        out = [{"section": k, "change": "new" if k not in base else "changed"}
               for k, s in cur.items() if k not in base or base[k]["hash"] != s["hash"]]
        out += [{"section": k, "change": "removed"} for k in base if k not in cur]
        return sorted(out, key=lambda c: c["section"])

    def status(self) -> dict:
        ch = self.changes()
        return {"state": "ok" if not ch else "maintenance_required", "unverified_sections": ch,
                "baseline_reviewed_by": self.baseline.get("reviewed_by"),
                "baseline_reviewed_at": str(self.baseline.get("reviewed_at") or "")}

    def rebaseline(self, reviewer: str, note: str, approvers: list[dict], audit) -> Path:
        """The last step of a policy maintenance release, checked and audited.

        Only an active policy.baseline_roles person (authority register) may sign off; the note is required. Every
        attempt is written to the hash-chained audit trail: ``policy.baseline_denied`` or ``policy.baseline_recorded``
        (who, role, why, and every section that changed with its old and new hash)."""
        roles = self.settings.get("policy.baseline_roles") or ["Financial Controller", "Chief Financial Officer"]
        user = next((u for u in approvers if u["user_id"] == reviewer and u.get("active", True)), None)
        if user is None or user.get("role") not in roles or len((note or "").strip()) < 10:
            reason = "note of at least 10 characters required" if user and user.get("role") in roles \
                else f"reviewer must be an active {' or '.join(roles)}"
            audit.record("policy.baseline_denied", actor=reviewer or "unknown", outcome="forbidden",
                         details={"reason": reason, "allowed_roles": roles})
            raise PermissionError(reason)
        old = {k: v.get("hash") for k, v in (self.baseline.get("sections") or {}).items()}
        cur = {k: v["hash"] for k, v in self.current_sections().items()}
        changed = [{"section": c["section"], "change": c["change"], "old_hash": old.get(c["section"]),
                    "new_hash": cur.get(c["section"])} for c in self.changes()]
        path = self.write_baseline(reviewer, note)
        audit.record("policy.baseline_recorded", actor=reviewer, outcome="ok",
                     details={"role": user["role"], "note": note[:500], "sections_changed": changed,
                              "sections_total": len(cur)})
        return path

    def write_baseline(self, reviewer: str, note: str) -> Path:
        """Write the file: record the CURRENT texts as verified against the controls. Use rebaseline() (checked, audited)."""
        refs = defaultdict(list)
        for n, s in REGISTRY.items():
            for d, x in s.compliance_refs:
                refs[f"{d}#{x}"].append(n)
        for n, s in CATALOG.items():
            if s.cite:
                refs[s.cite].append(f"param:{n}")
        old = self.baseline.get("sections", {})
        secs = {}
        for k, s in sorted(self.current_sections().items(), key=lambda kv: (kv[1]["document_id"], int(kv[0].split("#s")[1]))):
            basis = old.get(k, {}).get("basis") or ("enforced by " + ", ".join(sorted(set(refs[k]))) if refs.get(k)
                                                    else "informational / not automated")
            secs[k] = {"version": s["version"], "title": s["title"], "hash": s["hash"], "basis": basis}
        doc = {"reviewed_by": reviewer, "reviewed_at": self.settings.now().date().isoformat(), "note": note, "sections": secs}
        header = ("# Policy baseline: the section texts the code-based controls (skills/) were verified against.\n"
                  "# If the corpus differs from this, every case is held until maintenance is done (docs/governance.md).\n"
                  "# Regenerate only as the last step of a policy maintenance release:\n"
                  "#   finagent policy baseline --reviewer <id> --note <why>\n")
        self.baseline_path.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140), encoding="utf-8")
        self.baseline = doc
        return self.baseline_path
