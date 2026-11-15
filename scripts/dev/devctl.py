#!/usr/bin/env python3
"""Cross-platform dev controller (Windows, macOS, Linux) - same commands as the Makefile.

    python scripts/dev/devctl.py <command> [args]        (Windows: py scripts\\dev\\devctl.py ... or .\\dev.ps1 ...)

Commands
  bootstrap [--train]       NEW MACHINE, one command: setup -> (train embedding model) -> all tests -> all scenarios
                            with test-history export -> start the service -> load every case -> summary
  setup                     create .venv, install pinned deps, generate mock data, build the policy index
  start | stop | restart    run the API locally in the background (http://127.0.0.1:8080, Swagger /docs)
  status | logs             health check / show the last 100 log lines (logs -f to follow)
  up [--models auto|host|docker|mock]   WHOLE environment in Docker, one command: skill models + finagent (docker_up.py)
  down | down-clean         stop Docker (down-clean also deletes the data volume and pulled models)
  docker-test | docker-eval every test / all scenarios inside the running container
  docker-load [CASES] [--with-approvals]   submit cases to the running container
  api-smoke                 call every API operation (Swagger examples) against the running service
  dstatus | dlogs           Docker status / logs
  seed                      regenerate mock data, skill fixtures and the policy index
  load [CASES...]           submit scenario cases to the running API (all if none given)
  load-approve [CASES...]   submit cases and replay their approval decisions
  inbox [--auto approve]    approval inbox: receive approval webhooks, decide, send signed callbacks
  webhook-demo [CASES...]   self-contained webhook approval demo (auto-approve + replay test)
  skill-check               model acceptance gate: answer the skill questions with the real small + large models
  test-history [DIR]        run all scenarios; export inputs, outputs, reasoning, approvals, diagrams per case
  diagram RUN_ID [--format pdf png svg]   draw one run; eval-diagrams [pdf|png|svg]: draw every test scenario
  retrain [--embeddings docker|host|skip] [--force]   re-index + re-test + re-train after adding corpus docs / skills
  test | eval | demo        run tests / scenario evaluation / guided demo
  reset                     stop and delete local runtime data (./data)
Only the Python standard library is used here, so it runs before the venv exists.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIN = os.name == "nt"
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
RUN_DIR = ROOT / "data" / "run"
PID_FILE, LOG_FILE = RUN_DIR / "finagent.pid", RUN_DIR / "finagent.log"
PORT = os.environ.get("PORT", "8080")
HOST = os.environ.get("HOST", "127.0.0.1")
API = os.environ.get("API", f"http://127.0.0.1:{PORT}")


def run(*cmd, check=True, **kw) -> int:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, check=check, **kw).returncode


def py(*args, check=True) -> int:
    if not VPY.exists():
        sys.exit("virtual environment missing - run: python scripts/dev/devctl.py setup")
    return run(VPY, *args, check=check)


def fa(*args, check=True) -> int:
    return py("-m", "finagent.cli", *args, check=check)


def load_env() -> dict:
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def healthy(timeout=60) -> bool:
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(f"{API}/healthz", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def pid_alive(pid: int) -> bool:
    if WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_pid() -> int | None:
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip() or 0)
        if pid and pid_alive(pid):
            return pid
    return None


# ------------------------------------------------------------------ commands
def setup(_):
    if not VPY.exists():
        run(sys.executable, "-m", "venv", VENV)
    py("-m", "pip", "install", "-q", "--upgrade", "pip")
    py("-m", "pip", "install", "-q", "-r", "requirements.lock")
    if os.environ.get("SEMANTIC", "1") == "1":     # semantic search libraries; SEMANTIC=0 for keyword-only
        extra = ["--extra-index-url", "https://download.pytorch.org/whl/cpu"] if sys.platform.startswith("linux") else []
        py("-m", "pip", "install", "-q", *extra, "-r", "requirements-semantic.lock")
    py("-m", "pip", "install", "-q", "--no-deps", "-e", ".")
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
    seed(None)
    fa("workflow", "validate")
    print("\nReady. Next: python scripts/dev/devctl.py start")


def start(_):
    pid = running_pid()
    if pid:
        print(f"already running (pid {pid}) at {API}")
        return
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "data" / "index" / "bm25_index.json").exists():
        fa("ingest", check=False)
    log = open(LOG_FILE, "a", encoding="utf-8")
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if WIN else {"start_new_session": True}
    proc = subprocess.Popen([str(VPY), "-m", "finagent.cli", "serve", "--host", HOST, "--port", PORT], cwd=ROOT,
                            stdout=log, stderr=subprocess.STDOUT, env=load_env(), **kw)
    PID_FILE.write_text(str(proc.pid))
    if healthy():
        print(f"started (pid {proc.pid}) - API {API} - Swagger {API}/docs")
    else:
        sys.exit(f"service did not become healthy - see {LOG_FILE}")


def stop(_):
    pid = running_pid()
    if not pid:
        print("not running")
    else:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(40):                      # wait until the old server has released the port
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.5)
            else:
                os.kill(pid, signal.SIGKILL)
        print("stopped")
    PID_FILE.unlink(missing_ok=True)


def restart(a):
    stop(a)
    time.sleep(1)
    start(a)


def status(_):
    pid = running_pid()
    print(f"local: {'running (pid %s)' % pid if pid else 'stopped'}")
    try:
        with urllib.request.urlopen(f"{API}/healthz", timeout=3) as r:
            print(r.read().decode())
    except OSError:
        print(f"API {API} not reachable")


def logs(a):
    if not LOG_FILE.exists():
        sys.exit("no log yet - start the service first")
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-100:]))
    if a and a[0] == "-f":
        with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)
            while True:
                line = fh.readline()
                print(line, end="") if line else time.sleep(0.5)


def up(a):
    """Whole environment in Docker: models (host Ollama, an Ollama container, or mock) + finagent. See docker_up.py."""
    run(sys.executable, str(ROOT / "scripts" / "dev" / "docker_up.py"), *(a or []))


def down(_):
    run("docker", "compose", "--profile", "ollama", "down")


def down_clean(_):
    run("docker", "compose", "--profile", "ollama", "down", "-v")


def docker_test(_):
    run("docker", "compose", "exec", "-T", "finagent", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", check=False)


def docker_eval(_):
    run("docker", "compose", "exec", "-T", "finagent", "finagent", "eval", check=False)


def api_smoke(_):
    run(sys.executable, str(ROOT / "scripts" / "dev" / "api_smoke.py"), check=False)


def docker_load(a):
    run("docker", "compose", "exec", "-T", "finagent", "python", "scripts/dev/load_cases.py", *(a or []), check=False)


def dstatus(_):
    run("docker", "compose", "ps", check=False)


def dlogs(_):
    run("docker", "compose", "logs", "-f", "--tail", "100", "finagent", check=False)


def seed(_):
    py("scripts/generate_mock_data.py")
    py("scripts/skills/write_fixtures.py")
    fa("ingest")


def load(a):
    py("scripts/dev/load_cases.py", *(a or []), check=False)


def load_approve(a):
    py("scripts/dev/load_cases.py", "--with-approvals", *(a or []), check=False)


def inbox(a):
    py("scripts/dev/approval_inbox.py", *(a or []), check=False)


def webhook_demo(a):
    py("scripts/dev/approval_inbox.py", "--demo", *(a or []), "--duplicate", check=False)


def skill_check(a):
    py("scripts/dev/skill_model_check.py", *(a or []), check=False)




def test_history(a):
    fa("eval", "--history", *(a or []), check=False)


def diagram(a):
    fa("diagram", *(a or []), check=False)


def eval_diagrams(a):
    fa("eval", "--diagrams", *(a or ["pdf"]), check=False)


def retrain(a):
    py("scripts/training/retrain.py", *(a or []))


def test(a):
    py("-m", "pytest", "-q", *(a or []))


def evaluate(a):
    fa("eval", *(a or []), check=False)


def demo(_):
    fa("demo")


def bootstrap(a):
    """New machine -> everything running and verified, in one command:
    setup (venv, pinned deps, mock data, index) -> [optional: train the embedding model] -> all tests ->
    all 77 scenarios with a full test-history export -> start the service -> load every case."""
    a = list(a or [])
    train = "--train" in a or os.environ.get("TRAIN") == "1"
    if "--skip-setup" not in a:
        setup(None)
    if "--real-model" not in a:      # verify the install quickly; the real model is used by `make start` / skill-check
        os.environ["FINAGENT__SKILLS__LLM__USE_MOCK"] = "true"
    steps = []
    if train:
        rc = py("scripts/training/retrain.py", check=False)
        steps.append(("train embedding model", rc))
    elif not (ROOT / "training" / "embeddings" / "model" / "model.safetensors").exists():
        print("\n[bootstrap] no trained embedding model (not in git) -> search runs keyword-only (BM25). "
              "Add --train (or TRAIN=1) to train it (~8 min CPU).")
    steps.append(("tests (unit, contract, API, skill fixtures, scenarios)", py("-m", "pytest", "-q", check=False)))
    steps.append(("all scenarios + test history export", fa("eval", "--history", check=False)))
    start(None)
    load([])
    steps.append(("service running + cases loaded", 0))
    print("\n" + "=" * 72 + "\nbootstrap summary")
    for name, rc in steps:
        print(f"  {'OK  ' if rc == 0 else 'FAIL'}  {name}")
    latest = ROOT / "test_history" / "LATEST.txt"
    if latest.exists():
        print(f"  test history : test_history/{latest.read_text().strip()}/index.html")
    print(f"  API / Swagger: {API}/docs\n  stop with    : python scripts/dev/devctl.py stop  (make stop / .\\dev.ps1 stop)")
    sys.exit(0 if all(rc == 0 for _, rc in steps) else 1)


def reset(_):
    stop(None)
    if input("Delete local runtime data in ./data (runs, approvals, audit, index)? [y/N] ").strip().lower() == "y":
        shutil.rmtree(ROOT / "data", ignore_errors=True)
        print("deleted")


COMMANDS = {"bootstrap": bootstrap, "setup": setup, "start": start, "stop": stop, "restart": restart, "status": status, "logs": logs, "up": up,
            "down": down, "down-clean": down_clean, "docker-test": docker_test, "docker-eval": docker_eval, "docker-load": docker_load, "api-smoke": api_smoke, "dstatus": dstatus, "dlogs": dlogs, "seed": seed, "load": load,
            "load-approve": load_approve, "inbox": inbox, "retrain": retrain, "diagram": diagram, "test-history": test_history, "skill-check": skill_check, "eval-diagrams": eval_diagrams, "webhook-demo": webhook_demo, "test": test, "eval": evaluate, "demo": demo, "reset": reset}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
