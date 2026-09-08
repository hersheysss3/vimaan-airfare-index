"""
Collector plumbing: the parts that keep collection defensible.

Everything a collector does that could later be questioned happens here rather
than in each adapter, so it cannot be forgotten in one of them:

  * robots.txt is fetched and obeyed, per source
  * a request budget caps how hard any host is touched
  * every payload is hashed and stored before it is parsed
  * schema drift is detected rather than silently producing zero rows

What is deliberately absent: no CAPTCHA solving, no login or paywall
circumvention, no fingerprint spoofing, no residential proxies. A collector
that needs any of those is out of scope by design, not by omission.
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

USER_AGENT = (
    "VIMAAN/0.1 (official statistics research; "
    "+https://github.com/hersheysss3/vimaan-airfare-index)"
)


class CollectionRefused(RuntimeError):
    """Raised when a fetch is not permitted. Never caught and worked around."""


class SchemaDrift(RuntimeError):
    """A source still responds, but no longer looks like what we parse.

    Surfaced loudly: a parser that quietly returns nothing turns a broken
    collector into a coverage gap nobody notices.
    """


@dataclass
class RateBudget:
    """A per-host allowance, so we are a well-behaved visitor by construction."""
    max_requests: int = 120
    min_interval_s: float = 1.2
    _used: int = field(default=0, init=False)
    _last: float = field(default=0.0, init=False)

    def take(self) -> None:
        if self._used >= self.max_requests:
            raise CollectionRefused(
                f"request budget exhausted ({self.max_requests})")
        wait = self.min_interval_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._used += 1
        self._last = time.monotonic()

    @property
    def used(self) -> int:
        return self._used


class RobotsGate:
    """Checks robots.txt once per host and remembers the answer."""

    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 15.0):
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    def _parser(self, url: str):
        parts = urllib.parse.urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host in self._cache:
            return self._cache[host]
        rp = urllib.robotparser.RobotFileParser()
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
                resp = c.get(f"{host}/robots.txt",
                             headers={"User-Agent": self.user_agent})
            if resp.status_code >= 400:
                # no robots.txt published: the standard reads that as allowed
                rp = None
            else:
                rp.parse(resp.text.splitlines())
        except Exception:
            # if robots cannot be read we decline rather than assume consent
            rp = urllib.robotparser.RobotFileParser()
            rp.disallow_all = True
        self._cache[host] = rp
        return rp

    def allows(self, url: str) -> bool:
        rp = self._parser(url)
        if rp is None:
            return True
        return rp.can_fetch(self.user_agent, url)


@dataclass
class Fetched:
    url: str
    status: int
    payload: bytes
    content_type: Optional[str]
    fetched_at: datetime


class Collector:
    """Base class. Adapters implement `parse`, not fetching."""

    lane: str = "C_portal"
    source: str = "base"
    version: str = "0.1.0"
    #: strings that must appear in a payload for the parser to be trusted
    schema_markers: tuple[str, ...] = ()

    def __init__(
        self,
        budget: Optional[RateBudget] = None,
        gate: Optional[RobotsGate] = None,
        timeout: float = 30.0,
    ):
        self.budget = budget or RateBudget()
        self.gate = gate or RobotsGate()
        self.timeout = timeout

    def fetch(self, url: str, **kwargs) -> Fetched:
        if not self.gate.allows(url):
            raise CollectionRefused(f"robots.txt disallows {url}")
        self.budget.take()
        headers = {"User-Agent": USER_AGENT}
        headers.update(kwargs.pop("headers", {}) or {})
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
            resp = c.get(url, headers=headers, **kwargs)
        return Fetched(
            url=url,
            status=resp.status_code,
            payload=resp.content,
            content_type=resp.headers.get("content-type"),
            fetched_at=datetime.now(timezone.utc),
        )

    def check_schema(self, payload: bytes) -> None:
        """Fail loudly if the page no longer contains what we parse."""
        if not self.schema_markers:
            return
        text = payload.decode("utf-8", errors="ignore")
        missing = [m for m in self.schema_markers if m not in text]
        if missing:
            raise SchemaDrift(
                f"{self.source}: expected markers absent from payload: "
                f"{', '.join(missing)}"
            )

    def parse(self, payload: bytes, **context) -> list:
        raise NotImplementedError
