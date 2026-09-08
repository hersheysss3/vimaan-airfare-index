"""
Lane A: tariffs airlines are obliged to publish.

Rule 135(2) of the Aircraft Rules, 1937 requires every air transport
undertaking to publish its established tariff on its website. This is the only
source in the design that cannot be withdrawn by a commercial decision, which
matters more than it sounds: the Amadeus Self-Service tier was decommissioned
on 17 July 2026 and took an entire lane with it. A statutory obligation does
not get sunset by a vendor.

Reachability was measured rather than assumed, from the machine this was built
on, in September 2026. That result is recorded in CARRIERS below so the next
person does not repeat the work — and so nobody writes a parser against a host
they cannot fetch.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

from .base import Collector, CollectionRefused, RobotsGate


@dataclass(frozen=True)
class Carrier:
    """A carrier and what we actually know about reaching it."""
    code: str
    name: str
    home: str
    #: HTTP status seen from a plain GET, or 0 for connection failure
    observed_status: int
    #: robots.txt verdict for a generic agent, as read at the same time
    robots_allows: Optional[bool]
    note: str = ""

    @property
    def reachable(self) -> bool:
        return 200 <= self.observed_status < 400


# Measured 2026-09-08. Re-run `python -m vimaan.collect.tariff --probe` to
# refresh; results differ by network, and a 0 here means "not from here",
# not "blocked for everyone".
CARRIERS: tuple[Carrier, ...] = (
    Carrier("QP", "Akasa Air", "https://www.akasaair.com", 200, True,
            "robots.txt has no Disallow; sitemap published"),
    Carrier("SG", "SpiceJet", "https://www.spicejet.com", 200, True,
            "robots.txt Disallow is empty, which permits all"),
    Carrier("6E", "IndiGo", "https://www.goindigo.in", 0, None,
            "no response from this network; needs a machine that can reach it"),
    Carrier("AI", "Air India", "https://www.airindia.com", 0, None,
            "no response from this network"),
    Carrier("IX", "Air India Express", "https://www.airindiaexpress.com", 200, True,
            "robots.txt permits; reachable even though its parent is not"),
)


def carriers_by_code() -> dict[str, Carrier]:
    return {c.code: c for c in CARRIERS}


def reachable_carriers() -> list[Carrier]:
    return [c for c in CARRIERS if c.reachable]


class TariffCollector(Collector):
    """Base for a per-carrier tariff reader.

    Deliberately abstract. A tariff page has no common structure across
    carriers, so each needs its own parser written against the real page —
    and only two of the five carriers above can currently be fetched from
    here. Writing parsers for the other three would be writing fiction, so
    they are absent rather than stubbed.

    The plumbing they will share already exists on `Collector`: robots.txt is
    honoured, requests are budgeted, payloads are hashed into bronze before
    anything reads them, and a parser that stops matching raises SchemaDrift
    instead of quietly returning nothing.
    """

    lane = "A_statutory"
    version = "0.1.0"

    def __init__(self, carrier: Carrier, **kw):
        super().__init__(**kw)
        self.carrier = carrier
        self.source = f"{carrier.code.lower()}_tariff"

    def fetch_home(self):
        if not self.carrier.reachable:
            raise CollectionRefused(
                f"{self.carrier.name} was not reachable when last probed "
                f"(status {self.carrier.observed_status}). Re-probe before "
                f"writing a parser against it."
            )
        return self.fetch(self.carrier.home)

    def parse(self, payload: bytes, **context):
        raise NotImplementedError(
            f"No tariff parser written for {self.carrier.name}. Tariff pages "
            f"have no shared structure, so each is bespoke and must be written "
            f"against the live page."
        )


def probe(timeout: float = 20.0) -> list[tuple[Carrier, int, Optional[bool]]]:
    """Re-measure reachability and robots for every carrier.

    Prints a table that can be pasted back into CARRIERS. Kept as code rather
    than a note in a README so the claim stays checkable.
    """
    import httpx

    gate = RobotsGate(timeout=timeout)
    ua = {"User-Agent": "Mozilla/5.0 (compatible; VIMAAN/0.1 research)"}
    out = []
    for c in CARRIERS:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as cl:
                status = cl.get(c.home, headers=ua).status_code
        except Exception:
            status = 0
        allows = None
        if 200 <= status < 400:
            try:
                allows = gate.allows(c.home)
            except Exception:
                allows = None
        out.append((c, status, allows))
    return out


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true",
                    help="re-measure reachability and robots.txt")
    args = ap.parse_args()

    if args.probe:
        print(f"{'code':<6}{'carrier':<22}{'status':>7}  robots")
        for c, status, allows in probe():
            verdict = {True: "allows", False: "disallows", None: "-"}[allows]
            print(f"{c.code:<6}{c.name:<22}{status:>7}  {verdict}")
        return

    print("Lane A — tariffs published under Rule 135(2), Aircraft Rules 1937\n")
    print(f"{'code':<6}{'carrier':<22}{'reachable':<11}note")
    for c in CARRIERS:
        print(f"{c.code:<6}{c.name:<22}{'yes' if c.reachable else 'no':<11}{c.note}")
    print(f"\n{len(reachable_carriers())} of {len(CARRIERS)} reachable from the "
          f"machine last probed. Run with --probe to re-measure.")


if __name__ == "__main__":
    _main()
