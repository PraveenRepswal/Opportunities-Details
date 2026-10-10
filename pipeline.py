"""Unified pipeline CLI: LLM server + FastAPI + Streamlit launcher.

Usage:
    python pipeline.py             # launch LLM server + API + UI directly
    python pipeline.py --seed      # optional offline SLM profile extraction
    python pipeline.py -i          # interactive menu
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import httpx  # noqa: E402

from config import settings  # noqa: E402


# --------------------------------------------------------------------------- #
# llama-server lifecycle
# --------------------------------------------------------------------------- #
def start_llama_server(
    model_path: str,
    port: int,
    ctx: int = 8192,
    parallel: int = 2,
    reasoning_budget: Optional[int] = None,
) -> subprocess.Popen:
    exe = shutil.which("llama-server")
    if not exe:
        raise SystemExit("llama-server not found on PATH. Install llama.cpp or add it to PATH.")
    if not Path(model_path).exists():
        raise SystemExit(f"Model file not found: {model_path}")
    budget = reasoning_budget if reasoning_budget is not None else settings.model.reasoning_budget
    cmd = [exe, "-m", model_path, "--port", str(port), "-c", str(ctx), "-np", str(parallel)]
    if budget and budget > 0:
        cmd.extend(["--reasoning-budget", str(budget)])
    print(f"[pipeline] Starting llama-server on :{port} with {Path(model_path).name} (ctx={ctx}, reasoning_budget={budget})")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_server(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def stop(proc: Optional[subprocess.Popen]) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------- #
# Extraction Phase (SLM with regex fallback)
# --------------------------------------------------------------------------- #
def run_extraction_phase() -> None:
    """Run opportunity extraction using extract.py."""
    from extract import run_extraction
    asyncio.run(run_extraction())


# --------------------------------------------------------------------------- #
# App Phase
# --------------------------------------------------------------------------- #
def run_app_phase() -> None:
    port = settings.model.llamacpp_server_port
    llm = start_llama_server(settings.model.resolved_llm_model_path, port, ctx=8192, parallel=1)
    api = ui = None
    try:
        if not wait_for_server(port):
            raise SystemExit("LLM server failed to start.")
        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", settings.api.host, "--port", str(settings.api.port), "--no-access-log"]
        )
        ui = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py"])
        print(f"[pipeline] API: http://{settings.api.host}:{settings.api.port}/docs | UI: http://localhost:8501")
        print("[pipeline] Press Ctrl+C to stop everything.")
        api.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (ui, api, llm):
            stop(p)


def interactive() -> None:
    menu = {
        "1": ("Launch app (LLM + API + UI)", run_app_phase),
        "2": ("Extract profiles (SLM + regex fallback)", run_extraction_phase),
        "q": ("Quit", None),
    }
    for key, (label, _) in menu.items():
        print(f"  [{key}] {label}")
    choice = input("Select: ").strip().lower()
    action = menu.get(choice, (None, None))[1]
    if action:
        action()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extract", "--seed", dest="extract", action="store_true", help="Run extraction (SLM + regex fallback)")
    ap.add_argument("-i", "--interactive", action="store_true", help="Interactive menu")
    args = ap.parse_args()

    if args.interactive:
        interactive()
    elif args.extract:
        run_extraction_phase()
    else:
        run_app_phase()


if __name__ == "__main__":
    main()

