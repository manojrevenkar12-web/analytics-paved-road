"""Admission control: a bounded number of queries per lane and per user. A query that cannot get
a slot within the queue timeout is refused with 429 and a retry hint, instead of piling onto a
saturated engine and making everyone slow. One user's storm cannot take more than their share."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apr import conf
from apr.gateway import metrics
from apr.gateway.planner import Refused


class Admission:
    def __init__(self, cfg: dict | None = None):
        g = cfg or conf.read("platform")["gateway"]
        c = g["concurrency"]
        self.timeouts = {k: float(v) for k, v in g["queue_timeout_s"].items()}
        self.lanes = {"fast": asyncio.Semaphore(int(c["fast"])), "spark": asyncio.Semaphore(int(c["spark"]))}
        self.per_user = int(c["per_user"])
        self.users: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(self.per_user))
        self.waiting: defaultdict[str, int] = defaultdict(int)

    @asynccontextmanager
    async def slot(self, lane: str, user: str) -> AsyncIterator[float]:
        """Yields the seconds spent queueing."""
        t0 = time.perf_counter()
        deadline = t0 + self.timeouts[lane]
        user_sem, lane_sem = self.users[user], self.lanes[lane]
        self.waiting[lane] += 1
        metrics.QUEUED.labels(lane).set(self.waiting[lane])
        got_user = got_lane = False
        try:
            try:
                await asyncio.wait_for(user_sem.acquire(), max(0.0, deadline - time.perf_counter()))
                got_user = True
            except TimeoutError:
                raise Refused(
                    "user_concurrency",
                    f"{user} already has {self.per_user} queries running; wait for one to finish.",
                    429,
                ) from None
            try:
                await asyncio.wait_for(lane_sem.acquire(), max(0.0, deadline - time.perf_counter()))
                got_lane = True
            except TimeoutError:
                raise Refused("queue_full", f"The {lane} lane is at capacity; retry in a few seconds.", 429) from None
            self.waiting[lane] -= 1
            metrics.QUEUED.labels(lane).set(self.waiting[lane])
            yield time.perf_counter() - t0
        finally:
            if not got_lane:
                self.waiting[lane] -= 1
                metrics.QUEUED.labels(lane).set(self.waiting[lane])
            if got_lane:
                lane_sem.release()
            if got_user:
                user_sem.release()
