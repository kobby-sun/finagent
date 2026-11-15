#!/usr/bin/env python3
"""Generate docs/infrastructure.md from scripts/infra_catalog.py (same source as the PDF pages)."""
from pathlib import Path

from infra_catalog import COMPONENTS, SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
md = ["# Infrastructure: components, reasons and alternatives", "",
      "_Generated from `scripts/infra_catalog.py` (also used by pages 19-24 of "
      "[diagrams/system_detailed_design.pdf](diagrams/system_detailed_design.pdf) and the presentation). Regenerate: `make diagrams`._", "",
      "## Diagram", "", "```mermaid", "flowchart LR",
      '  subgraph OUT["Outside"]',
      '    U["AP team / CLI (2)"]', '    A["Approval systems (14)"]',
      '    M["Model providers (12)"]', '    S["Systems of record - mock (15)"]', "  end",
      '  subgraph LOCAL["Local / laptop - Docker Compose (16)"]',
      '    FA["finagent container: API (1), engine (3), skills (4), contracts (5)"]',
      '    OL["Ollama container (11, 12) - optional"]', '    TR["Training container (13)"]',
      '    DATA[("./data: SQLite store (6), audit (7), BM25 (9), vectors (10)")]', '    COR[("Policy corpus (8)")]', "  end",
      '  subgraph AWS["AWS - Terraform (17)"]',
      '    subgraph VPC["VPC (18)"]', '      ALB["ALB (19)"] --> ECS["ECS Fargate task (20)"]',
      '      ECS --> EFS[("EFS (21)")]', '      SCH["EventBridge Scheduler (28)"] -->|RunTask| ECS',
      '      IAM["IAM roles (25)"]', '      SM["Secrets Manager (24)"] --> ECS', "    end",
      '    ECS --> BR["Bedrock (26)"]', '    ECS --> CW["CloudWatch Logs (27)"]', '    ECR["ECR (22)"] --> ECS',
      '    S3[("S3 corpus bucket (23)")] -.sync at build.-> ECR', "  end",
      "  U --> FA", "  A <--> FA", "  M -.-> FA", "  S --> FA", "  FA --> DATA", "  FA --> COR",
      "  OL -.-> FA", "  TR --> DATA", "  FA -.same image.-> ECS", "```", "",
      "## Component catalogue", "",
      "| # | Component | Layer | What it does | Why we chose it | Alternatives -> when to switch |",
      "|---|---|---|---|---|---|"]
for no, layer, comp, what, why, alt in COMPONENTS:
    md.append(f"| {no} | **{comp}** | {layer} | {what} | {why} | {alt} |")
md += ["", "## When circumstances change", "", "| If this changes ... | ... change these components (numbers = catalogue) |", "|---|---|"]
for when, what in SCENARIOS:
    md.append(f"| {when} | {what} |")
md += ["", "**What stays the same in every case:** skills, data contracts, the approval gate, the audit trail and the scenario tests. "
       "Any swap is verified by the same 80 scenarios (`finagent eval`)."]
(ROOT / "docs" / "infrastructure.md").write_text("\n".join(md) + "\n")
print("wrote docs/infrastructure.md")
