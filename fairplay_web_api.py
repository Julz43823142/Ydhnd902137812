"""HTTP adapter for the original engine-free Insight v87 review.

Deploy this as a persistent Python service; the Cloudflare Pages frontend
cannot execute Python. No results are written to disk.
"""
import asyncio
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fairplay_method3 import review, VERSION

app = FastAPI(title="Shark Insight v87 API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fairplaychess.pages.dev"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
executor = ThreadPoolExecutor(max_workers=2)
jobs = {}
lock = Lock()
MAX_JOBS = 30
TTL_SECONDS = 1200
NAME = re.compile(r"^[A-Za-z0-9_-]{3,25}$")


class ReviewRequest(BaseModel):
    username: str


def clean(value, depth=0):
    """JSON-safe conversion without changing the v87 calculations."""
    if depth > 12:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if __import__("math").isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [clean(v, depth + 1) for v in value[:500]]
    if isinstance(value, dict):
        return {str(k): clean(v, depth + 1) for k, v in list(value.items())[:300]}
    return str(value)


def worker(job_id, username):
    def progress(message):
        with lock:
            if job_id in jobs:
                jobs[job_id]["progress"] = str(message)[:180]

    try:
        result = review(username, progress=progress)
        method = (result.diagnostics or {}).get("method3") or {}
        payload = {
            "version": result.version,
            "username": result.username,
            "priority": result.priority,
            "confidence": result.confidence,
            "games": len(result.games),
            "selected_games": result.selected_games,
            "partial": result.partial,
            "reasons": clean(result.reasons),
            "method3": clean(method),
            "engine_used": False,
        }
        with lock:
            if job_id in jobs:
                jobs[job_id].update(state="complete", result=payload)
    except Exception:
        with lock:
            if job_id in jobs:
                jobs[job_id].update(state="failed", error="Review failed or timed out. Try again later.")


@app.get("/health")
def health():
    return {"ok": True, "version": VERSION}


@app.post("/review", status_code=202)
async def start_review(body: ReviewRequest, request: Request):
    username = body.username.strip()
    if not NAME.fullmatch(username):
        raise HTTPException(400, "Invalid Chess.com username.")
    now = time.monotonic()
    with lock:
        for key in list(jobs):
            if now - jobs[key]["created"] > TTL_SECONDS:
                del jobs[key]
        running = sum(j["state"] in ("queued", "running") for j in jobs.values())
        if running >= 2 or len(jobs) >= MAX_JOBS:
            raise HTTPException(429, "Review service busy. Try again later.")
        job_id = secrets.token_urlsafe(24)
        jobs[job_id] = {
            "state": "running", "created": now, "progress": "Starting v87 review…",
            "username": username,
        }
    asyncio.get_running_loop().run_in_executor(executor, worker, job_id, username)
    return {"job_id": job_id, "state": "running", "version": VERSION}


@app.get("/review/{job_id}")
def get_review(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job or time.monotonic() - job["created"] > TTL_SECONDS:
            raise HTTPException(404, "Review job expired or not found.")
        return {k: v for k, v in job.items() if k != "created"}
