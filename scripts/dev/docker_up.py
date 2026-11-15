#!/usr/bin/env python3
"""ONE command for the whole environment in Docker (macOS, Windows, Linux - standard library only, no venv needed).

    make up                                   macOS / Linux
    .\\dev.ps1 up                              Windows PowerShell
    python scripts/dev/docker_up.py           any OS (or: python scripts/dev/devctl.py up)

Steps: pick where the skill models run -> pull any that are missing -> build + start finagent (the trained search model
is inside the image) -> wait until healthy -> print what it is using.

    --models auto    (default) Ollama on this machine if it is running, else an Ollama container
    --models host    Ollama on this machine (fastest on a Mac: Ollama in Docker has no GPU there)
    --models docker  Ollama in a container; the models are pulled into a Docker volume (~14 GB the first time)
    --models mock    no models: deterministic mock answers (quick demo; same as the test suite)
(or the MODELS environment variable). Stop: make down / .\\dev.ps1 down. Remove data and pulled models: down-clean.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SMALL = os.environ.get("SMALL", "qwen2.5:7b-instruct")
LARGE = os.environ.get("LARGE", "qwen2.5:14b-instruct")
PORT = os.environ.get("PORT", "8080")
API = f"http://127.0.0.1:{PORT}"
HOST_OLLAMA = "http://127.0.0.1:11434"


def get_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def compose(*args: str, env: dict, check: bool = True, capture: bool = False):
    cmd = ["docker", "compose", *args]
    res = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=capture)
    if check and res.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}" + (f"\n{res.stderr}" if capture else ""))
    return res


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Spin up the whole finagent environment in Docker")
    ap.add_argument("--models", choices=["auto", "host", "docker", "mock"], default=os.environ.get("MODELS", "auto"))
    a = ap.parse_args(argv)
    if shutil.which("docker") is None:
        sys.exit("Docker is not installed or not on PATH (Docker Desktop on macOS / Windows, Docker Engine on Linux)")
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")

    models = a.models
    if models == "auto":
        models = "host" if get_json(f"{HOST_OLLAMA}/api/tags") is not None else "docker"
    env = {**os.environ, "USE_MOCK_MODELS": "false"}
    profile: list[str] = []

    if models == "host":
        tags = get_json(f"{HOST_OLLAMA}/api/tags")
        if tags is None:
            sys.exit("--models host: Ollama is not running on this machine (start it, or use --models docker)")
        env["OLLAMA_URL"] = "http://host.docker.internal:11434"
        have = {m["name"] for m in tags.get("models", [])}
        for m in (SMALL, LARGE):
            if m not in have:
                print(f"pulling {m} on this machine ...")
                if shutil.which("ollama") is None or subprocess.run(["ollama", "pull", m]).returncode != 0:
                    sys.exit(f"could not pull {m}: run `ollama pull {m}` yourself, or use --models docker")
    elif models == "docker":
        env["OLLAMA_URL"] = "http://ollama:11434"
        profile = ["--profile", "ollama"]
        compose(*profile, "up", "-d", "ollama", env=env)
        for _ in range(60):
            if compose("exec", "-T", "ollama", "ollama", "list", env=env, check=False, capture=True).returncode == 0:
                break
            time.sleep(1)
        listed = compose("exec", "-T", "ollama", "ollama", "list", env=env, check=False, capture=True).stdout or ""
        for m in (SMALL, LARGE):
            if not any(line.split()[:1] == [m] for line in listed.splitlines()):
                print(f"pulling {m} into the Ollama container (first time only) ...")
                compose("exec", "-T", "ollama", "ollama", "pull", m, env=env)
    else:  # mock
        env.update(OLLAMA_URL="http://ollama:11434", USE_MOCK_MODELS="true")

    print(f"building and starting finagent (models: {models}) ...")
    compose(*profile, "up", "-d", "--build", "finagent", env=env)
    h = None
    for _ in range(120):
        h = get_json(f"{API}/healthz")
        if h:
            break
        time.sleep(2)
    if not h:
        sys.exit("finagent did not become healthy - see: docker compose logs finagent")

    r, q = h["retrieval"], h.get("skill_questions") or {}

    def tier(t):
        if not t:
            return "-"
        ok = t.get("reachable") and t.get("model_pulled") is not False
        return f"{t['model']} ({'ok' if ok else 'NOT READY'})"

    print("finagent is up")
    print(f"  API      {API}   Swagger {API}/docs")
    print(f"  search   {r['mode']}" + (f" ({r['embedder']})" if r.get("embedder") else "")
          + (f"  DEGRADED: {r['degraded']}" if r.get("degraded") else ""))
    print(f"  policy   {h.get('policy')}")
    print("  models   " + ("mock (deterministic)" if models == "mock"
                           else f"small {tier(q.get('small'))} | large {tier(q.get('large'))}"))
    print("  next     make docker-test | make docker-eval | make down   (Windows: .\\dev.ps1 docker-test / docker-eval / down)")


if __name__ == "__main__":
    main()
