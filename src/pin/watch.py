"""Continuous operator loop: poll Technocore, fill at most N jobs per tick.

`pin match --live` is one tick. `pin watch --live` keeps the same matcher
in memory so it does not refill jobs it already paid. Paper holds no value.
Does not republish the roster and does not write kibble or lobby.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from pin.identity import PIN_OPERATOR_ROOM, TCLK_OFFERS_ROOM, Identity
from pin.lab import PinLab
from pin.matcher import MatchStep, OperatorMatcher, ingest_json_messages
from pin.technocore_client import fetch_room_json, post_signed_line

DEFAULT_INTERVAL_SEC = 20.0
DEFAULT_MAX_JOBS = 1


@dataclass
class WatchTick:
    n: int
    posted: int = 0
    quotes: int = 0
    receipts: int = 0
    tclk: int = 0
    skipped: int = 0
    since: int = 0
    live_posts: list[dict[str, str | int]] = field(default_factory=list)
    fetch_error: str | None = None
    live: bool = False
    holds_value: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick": self.n,
            "posted": self.posted,
            "quotes": self.quotes,
            "receipts": self.receipts,
            "tclk": self.tclk,
            "skipped": self.skipped,
            "since": self.since,
            "live_posts": self.live_posts,
            "fetch_error": self.fetch_error,
            "live": self.live,
            "holds_value": False,
            "seed": False,
        }


def post_step_lines(
    ident: Identity,
    step: MatchStep,
    *,
    base: str,
    nonce0: int | None = None,
) -> list[dict[str, str | int]]:
    posted: list[dict[str, str | int]] = []
    nonce = nonce0 if nonce0 is not None else int(time.time() * 1000)
    pin_lines = step.quotes + step.leaf0 + step.receipts
    tclk_lines = step.tclk_accepts + step.tclk_settles
    for i, line in enumerate(pin_lines):
        wr = post_signed_line(ident, room=PIN_OPERATOR_ROOM, text=line, nonce=str(nonce + i), base=base)
        posted.append({"room": PIN_OPERATOR_ROOM, "status": wr.status, "body": wr.body[:200]})
    nonce += len(pin_lines)
    for i, line in enumerate(tclk_lines):
        wr = post_signed_line(ident, room=TCLK_OFFERS_ROOM, text=line, nonce=str(nonce + i), base=base)
        posted.append({"room": TCLK_OFFERS_ROOM, "status": wr.status, "body": wr.body[:200]})
    return posted


def ingest_live(matcher: OperatorMatcher, *, base: str) -> None:
    pin_since = matcher.since if matcher.since else 0
    ingest_json_messages(matcher.venue, fetch_room_json(PIN_OPERATOR_ROOM, since=pin_since, base=base))
    ingest_json_messages(
        matcher.venue,
        fetch_room_json(TCLK_OFFERS_ROOM, since=None, limit=200, base=base),
        room=TCLK_OFFERS_ROOM,
    )


def run_watch(
    ident: Identity,
    *,
    lab: PinLab | None = None,
    matcher: OperatorMatcher | None = None,
    live: bool = False,
    base: str = "https://technocore.chat",
    interval: float = DEFAULT_INTERVAL_SEC,
    max_jobs: int = DEFAULT_MAX_JOBS,
    ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[WatchTick]:
    """Yield one tick at a time. `ticks=1` is `pin match`. Live is opt-in."""
    lab = lab or PinLab()
    matcher = matcher or OperatorMatcher(lab, ident)
    n = 0
    while True:
        n += 1
        tick = WatchTick(n=n, live=live)
        try:
            if live:
                ingest_live(matcher, base=base)
            step = matcher.step(max_jobs=max_jobs)
            tick.quotes = len(step.quotes)
            tick.receipts = len(step.receipts)
            tick.tclk = len(step.tclk_accepts) + len(step.tclk_settles)
            tick.skipped = len(step.skipped)
            tick.since = step.since
            tick.posted = step.as_dict()["posted"]
            if live and tick.posted:
                tick.live_posts = post_step_lines(ident, step, base=base)
        except httpx.HTTPError as exc:
            tick.fetch_error = exc.__class__.__name__
        yield tick
        if ticks is not None and n >= ticks:
            return
        if interval > 0:
            sleep(interval)
