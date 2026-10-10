#!/usr/bin/env python3
"""Cloud Scheduler adapter (design §10.6).

DELETE THIS FILE once api.py gains a real cadence route. This adapter exists only
because Cloud Scheduler sends a STATIC body and STATIC headers with no runtime
substitution, so it cannot compute the per-slot idempotency key that
api.py::_idempotency requires. A static key would make the fast loop replay its
first response forever. This router derives the key server-side and contains no
domain logic.

It is mounted at /_sched by deploy/asgi.py. It reads api.REPLAYS read-only; it
never creates a replay (inventing state on a cadence tick is worse than reporting
a gap). With process-local state (S2 unfixed) the fast loop is correct on one
instance and wrong on several, so the fast-loop Scheduler job stays PAUSED until
the Postgres adapter lands (residual risk R-19).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# India Standard Time is a FIXED UTC+5:30 offset with no DST, so a fixed-offset
# tzinfo is exactly "Asia/Kolkata" for every instant and avoids depending on the
# IANA tz database (tzdata is not in the frozen dependency set, and the Windows
# host has no system zoneinfo). On Linux the result is identical to
# ZoneInfo("Asia/Kolkata") because that zone has observed +05:30 continuously.
_IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def derive_slot_key(now: datetime) -> str:
    """`sched-YYYYMMDD-SS` for the 15-minute IST slot containing `now`.

    Stable across retries inside one slot, distinct across slots, 17 chars, and
    matches the product ID_RE pattern.
    """
    local = now.astimezone(_IST)
    slot = (local.hour * 60 + local.minute) // 15
    return f"sched-{local:%Y%m%d}-{slot:02d}"


def _metric_inc(name: str, **labels: str) -> None:
    from monsoon_grid import observability as obs

    inst = obs.default_registry().get(name)
    if inst is not None:
        try:
            inst.inc(**labels)  # type: ignore[attr-defined]
        except Exception:
            pass


def run_slow_loop(*, now: datetime, demo_fn: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Daily slow loop: one full demo replay, keyed by the slot of `now`."""
    from monsoon_grid import api
    from monsoon_grid.contracts import DemoRequest

    key = derive_slot_key(now)
    request = DemoRequest(idempotency_key=key)
    fn = demo_fn or api.demo
    response = fn(request, idempotency_key=key)
    return {"loop": "slow", "idempotency_key": key, "result": response}


def run_fast_loop(*, now: datetime, replays: dict[str, Any] | None = None, advance_fn: Callable[..., Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Fast loop: advance the single active replay, keyed by the slot of `now`.

    Returns (http_status, body). 409 with a cadence_skips_total increment when
    there is not exactly one advanceable replay.
    """
    from monsoon_grid import api
    from monsoon_grid.contracts import ReplayAdvanceRequest

    key = derive_slot_key(now)
    records = api.REPLAYS if replays is None else replays
    active = [(rid, rec) for rid, rec in records.items() if getattr(rec, "slot_index", 96) < 96]

    if len(active) == 0:
        _metric_inc("monsoongrid_cadence_skips_total", loop="fast", reason="no_active_replay")
        return 409, {"loop": "fast", "code": "NO_ACTIVE_REPLAY", "idempotency_key": key}
    if len(active) > 1:
        _metric_inc("monsoongrid_cadence_skips_total", loop="fast", reason="ambiguous")
        return 409, {"loop": "fast", "code": "AMBIGUOUS_ACTIVE_REPLAY", "idempotency_key": key}

    replay_id, record = active[0]
    expected_digest = api._state_digest(record)
    request = ReplayAdvanceRequest(
        replay_id=replay_id,
        expected_slot_index=record.slot_index,
        expected_state_digest=expected_digest,
        idempotency_key=key,
    )
    fn = advance_fn or api.advance_replay
    response = fn(replay_id, request, idempotency_key=key)
    return 200, {"loop": "fast", "idempotency_key": key, "replay_id": replay_id, "result": response}


def _build_router():
    from fastapi import Depends, Response
    from fastapi import APIRouter
    from datetime import timezone

    from monsoon_grid.auth import Principal, require_roles

    router = APIRouter()

    @router.post("/slow-loop")
    def slow_loop(principal: Principal = Depends(require_roles("operator"))) -> dict[str, Any]:
        return run_slow_loop(now=datetime.now(timezone.utc))

    @router.post("/fast-loop")
    def fast_loop(response: Response, principal: Principal = Depends(require_roles("operator"))) -> dict[str, Any]:
        status, body = run_fast_loop(now=datetime.now(timezone.utc))
        response.status_code = status
        return body

    return router


sched_router = _build_router()
