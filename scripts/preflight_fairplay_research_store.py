"""Validate the real encrypted research branch independently of the Discord worker.

Executed only in a trusted push-to-main workflow with repository write access.
Never log cohort identities, evidence, or credential-bearing Git stderr.
"""
import time

from fairplay_research_live import read_cohorts
from fairplay_research_store import ResearchRecordStore


def preflight(store_factory=ResearchRecordStore):
    _, digest = read_cohorts()
    store = store_factory()
    if not store.enabled:
        raise RuntimeError("Research encryption credentials unavailable")
    state = store.research_state()
    if not state:
        initial = {
            "digest": digest,
            "cursor": 0,
            "rows": [],
            "evidence": [],
            "archive_version": 3,
            "finished": False,
            "started_at": time.time(),
        }
        if not store.save_research(initial):
            raise RuntimeError("Encrypted research branch initialization failed")
        # Verify durability through a NEW store instance, not in-memory state.
        state = store_factory().research_state()
        if state != initial:
            raise RuntimeError("Encrypted research branch verification failed")
    if (state.get("digest") != digest or
            state.get("archive_version") != 3 or
            not isinstance(state.get("cursor"), int) or
            not 0 <= state["cursor"] <= 200 or
            len(state.get("rows", [])) != state["cursor"] or
            len(state.get("evidence", [])) != state["cursor"]):
        raise RuntimeError("Encrypted research branch metadata incompatible")
    print("Encrypted research branch verified at %d/200; no account information logged." %
          state["cursor"], flush=True)
    return state["cursor"]


if __name__ == "__main__":
    preflight()
