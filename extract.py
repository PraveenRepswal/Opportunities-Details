"""Dedicated Opportunity Extraction Pipeline: SLM with automatic Regex fallback.

Extracts structured metadata (deadline, location, organization, type, eligibility,
benefits, summary) for scraped opportunities.

Usage:
    python extract.py              # Extract all opportunities missing profiles
    python extract.py --all        # Re-extract all opportunities
    python extract.py --regex-only # Fast extraction using regex/heuristics only (no model)
    python extract.py --limit 20   # Process a maximum of 20 opportunities
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
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import httpx  # noqa: E402

from config import settings  # noqa: E402
from backend.database import (  # noqa: E402
    init_db,
    list_opportunities,
    update_opportunity_profile_and_metadata,
)
from backend.metadata_extractor import (  # noqa: E402
    build_fallback_profile,
    extract_metadata_rules,
    extract_profile_with_llamacpp,
    is_slm_available,
)


def start_slm_server(model_path: str, port: int, ctx: int = 2048, parallel: int = 2) -> Optional[subprocess.Popen]:
    """Start temporary llama-server instance for SLM extraction."""
    exe = shutil.which("llama-server")
    if not exe:
        print("[extract] llama-server binary not found on PATH.")
        return None
    if not Path(model_path).exists():
        print(f"[extract] SLM model file not found: {model_path}")
        return None

    print(f"[extract] Starting temporary llama-server on :{port} with {Path(model_path).name}...")
    try:
        return subprocess.Popen(
            [exe, "-m", model_path, "--port", str(port), "-c", str(ctx), "-np", str(parallel)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        print(f"[extract] Failed to spawn llama-server: {exc}")
        return None


def wait_for_server(port: int, timeout: float = 15.0, proc: Optional[subprocess.Popen] = None) -> bool:
    """Wait for llama-server to become responsive on /health."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc and proc.poll() is not None:
            err_msg = ""
            try:
                if proc.stderr:
                    err_msg = proc.stderr.read()[:300].strip()
            except Exception:
                pass
            detail = f": {err_msg}" if err_msg else ""
            print(f"[extract] llama-server process exited unexpectedly with code {proc.poll()}{detail}")
            return False
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).status_code in (200, 204):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def stop_server(proc: Optional[subprocess.Popen]) -> None:
    """Stop the temporary llama-server process and release GPU VRAM."""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[extract] Stopped temporary SLM server and released port / GPU VRAM.")


async def extract_item(
    opp_id: int,
    item: Dict[str, Any],
    slm_ready: bool,
    slm_port: int,
    semaphore: asyncio.Semaphore,
) -> Tuple[int, str]:
    """Extract profile for a single opportunity: tries SLM first, falls back to regex."""
    title = item.get("title") or item.get("name") or "Opportunity"
    content = item.get("content") or ""
    metadata = item.get("metadata") or {}

    async with semaphore:
        # 1. Attempt SLM extraction if server is responsive
        if slm_ready:
            try:
                server_url = f"http://127.0.0.1:{slm_port}"
                prof = await extract_profile_with_llamacpp(
                    title=title,
                    content=content,
                    server_url=server_url,
                    timeout=25.0,
                )
                if prof:
                    update_opportunity_profile_and_metadata(opp_id, prof.model_dump())
                    return (opp_id, "slm")
            except Exception:
                pass

        # 2. Heuristic Regex fallback (deterministic, fast)
        try:
            fallback_meta = metadata if metadata else extract_metadata_rules(title, content)
            prof = build_fallback_profile(title=title, content=content, metadata=fallback_meta)
            update_opportunity_profile_and_metadata(opp_id, prof.model_dump())
            return (opp_id, "regex")
        except Exception as exc:
            print(f"[extract] Error in fallback extraction for id={opp_id}: {exc}")
            return (opp_id, "failed")


async def run_extraction(
    force_all: bool = False,
    regex_only: bool = False,
    limit: Optional[int] = None,
    concurrency: int = 2,
    server_timeout: float = 15.0,
) -> Dict[str, int]:
    """Execute the extraction pipeline for database opportunities."""
    init_db()
    items = list_opportunities(limit=100000).get("items", [])

    if force_all:
        target_items = items
    else:
        target_items = [it for it in items if not it.get("profile")]

    if limit and limit > 0:
        target_items = target_items[:limit]

    total = len(target_items)
    print(f"\n[extract] Found {total} opportunities to process (force_all={force_all}).")
    if total == 0:
        print("[extract] All opportunities already have structured profiles.")
        return {"total": 0, "slm": 0, "regex": 0, "failed": 0}

    slm_port = settings.model.llamacpp_server_port
    slm_proc: Optional[subprocess.Popen] = None
    we_started_server = False
    slm_ready = False

    if not regex_only:
        # Check if llama-server is already running
        if await is_slm_available(f"http://127.0.0.1:{slm_port}"):
            print(f"[extract] Detected running llama-server on :{slm_port}. Using existing server.")
            slm_ready = True
        else:
            # Attempt to launch the SLM server
            slm_model = settings.model.resolved_slm_model_path
            slm_proc = start_slm_server(slm_model, slm_port)
            if slm_proc:
                print(f"[extract] Waiting up to {server_timeout}s for SLM server to become ready...")
                if wait_for_server(slm_port, timeout=server_timeout, proc=slm_proc):
                    slm_ready = True
                    we_started_server = True
                    print("[extract] SLM server is ready.")
                else:
                    print("[extract] SLM server did not become ready in time; using regex fallback.")
                    stop_server(slm_proc)
                    slm_proc = None
            else:
                print("[extract] Could not start SLM server; using regex fallback.")
    else:
        print("[extract] Running in --regex-only mode. Skipping SLM server.")

    stats = {"total": total, "slm": 0, "regex": 0, "failed": 0}
    start_time = time.time()
    semaphore = asyncio.Semaphore(concurrency)

    try:
        tasks = [
            extract_item(it["id"], it, slm_ready, slm_port, semaphore)
            for it in target_items
        ]
        # Execute concurrently with progress reporting
        done_count = 0
        for fut in asyncio.as_completed(tasks):
            opp_id, method = await fut
            stats[method] = stats.get(method, 0) + 1
            done_count += 1
            if done_count % 25 == 0 or done_count == total:
                # print(f"[extract] Progress: {done_count}/{total} (SLM: {stats['slm']}, Regex: {stats['regex']})")
                pass
    finally:
        if we_started_server and slm_proc:
            stop_server(slm_proc)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print("EXTRACTION SUMMARY")
    print(f"{'='*60}")
    print(f"Total Processed:    {stats['total']:4d}")
    print(f"Extracted via SLM:  {stats['slm']:4d}")
    print(f"Fallback to Regex:  {stats['regex']:4d}")
    if stats["failed"]:
        print(f"Failed:             {stats['failed']:4d}")
    print(f"Time Taken:         {elapsed:.1f}s")
    print(f"{'='*60}\n")

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", "--force", dest="force_all", action="store_true", help="Re-extract all opportunities, even if profiles exist")
    ap.add_argument("--regex-only", action="store_true", help="Skip SLM and extract using regex/heuristics only")
    ap.add_argument("--limit", type=int, default=None, help="Maximum number of opportunities to process")
    ap.add_argument("--concurrency", type=int, default=2, help="Concurrency level for SLM requests (default: 2)")
    ap.add_argument("--timeout", type=float, default=15.0, help="Timeout in seconds to wait for SLM server to boot")
    args = ap.parse_args()

    asyncio.run(
        run_extraction(
            force_all=args.force_all,
            regex_only=args.regex_only,
            limit=args.limit,
            concurrency=args.concurrency,
            server_timeout=args.timeout,
        )
    )


if __name__ == "__main__":
    main()
