#!/usr/bin/env python3
"""Export the Swagger/OpenAPI spec (docs/api/openapi.json + openapi.yaml) from the FastAPI app."""
import json
import tempfile
from pathlib import Path

import yaml

from finagent.api.app import create_app
from finagent.config import load_settings
from finagent.services import build_app

ROOT = Path(__file__).resolve().parents[1]
s = load_settings()
s = s.with_overrides({"app.data_dir": tempfile.mkdtemp(prefix="finagent-openapi-")})
spec = create_app(build_app(s)).openapi()
spec["servers"] = [{"url": "http://localhost:8080", "description": "local (finagent serve / docker compose)"},
                   {"url": "https://{alb_dns}", "description": "AWS ALB (terraform output api_url)", "variables": {"alb_dns": {"default": "example.elb.amazonaws.com"}}}]
spec.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKey"] = {"type": "apiKey", "in": "header", "name": "X-API-Key"}
out = ROOT / "docs" / "api"
out.mkdir(parents=True, exist_ok=True)
(out / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n")
(out / "openapi.yaml").write_text(yaml.safe_dump(json.loads(json.dumps(spec)), sort_keys=False, allow_unicode=True))
print(f"wrote {out/'openapi.json'} and openapi.yaml: {len(spec['paths'])} paths")
