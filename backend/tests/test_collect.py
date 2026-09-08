"""
The collector layer: the guarantees, and the parsers.

Nothing here touches the network. What is tested is that the plumbing which
keeps collection defensible actually holds, and that a parser turns a real
payload shape into the right rows — including refusing to run when a source
stops looking like itself.
"""
from datetime import date, datetime, timezone

import pytest

from vimaan.collect.base import (
    Collector, CollectionRefused, RateBudget, SchemaDrift,
)
from vimaan.collect.tariff import CARRIERS, TariffCollector, carriers_by_code, reachable_carriers
from vimaan.collect.travelpayouts import TravelpayoutsCollector
from vimaan.models import Lane


# ------------------------------------------------------------ rate budget
def test_budget_refuses_past_its_allowance():
    b = RateBudget(max_requests=2, min_interval_s=0)
    b.take()
    b.take()
    with pytest.raises(CollectionRefused):
        b.take()


def test_budget_counts_what_it_spent():
    b = RateBudget(max_requests=5, min_interval_s=0)
    b.take(); b.take()
    assert b.used == 2


# --------------------------------------------------------- schema drift
class _Probe(Collector):
    source = "probe"
    schema_markers = ("expected-marker",)

    def parse(self, payload: bytes, **ctx):
        self.check_schema(payload)
        return []


def test_missing_markers_raise_rather_than_return_nothing():
    """A parser that silently yields zero rows turns a break into a data gap."""
    with pytest.raises(SchemaDrift):
        _Probe().parse(b"<html>the page changed</html>")


def test_present_markers_pass():
    assert _Probe().parse(b"<html>expected-marker</html>") == []


def test_no_markers_declared_means_no_check():
    class Loose(Collector):
        def parse(self, payload, **ctx):
            self.check_schema(payload)
            return ["ok"]
    assert Loose().parse(b"anything") == ["ok"]


# ----------------------------------------------------------- travelpayouts
def _tp_payload():
    return (b'{"success":true,"currency":"inr","data":[{'
            b'"origin":"DEL","destination":"BOM",'
            b'"origin_airport":"DEL","destination_airport":"BOM",'
            b'"price":5432,"airline":"6E","flight_number":"2134",'
            b'"departure_at":"2026-10-15T07:35:00+05:30",'
            b'"transfers":0,"duration_to":135}]}')


def test_travelpayouts_parses_a_real_shape():
    col = TravelpayoutsCollector(token="x")
    fares = col.parse(_tp_payload(),
                      collected_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert len(fares) == 1
    f = fares[0]
    assert f.route == "BOM-DEL"
    assert f.price_total == 5432
    assert f.carrier == "6E"
    assert f.flight_no == "6E-2134"
    assert f.stops == 0
    assert f.duration_min == 135
    assert f.depart_slot == "morning"
    assert f.lane is Lane.LICENSED
    assert f.depart_date == date(2026, 10, 15)


def test_travelpayouts_drops_unusable_rows():
    """A row without a price or a parseable date is not a price observation."""
    payload = (b'{"currency":"inr","data":['
               b'{"origin_airport":"DEL","destination_airport":"BOM",'
               b'"price":null,"departure_at":"2026-10-15T07:00:00+05:30"},'
               b'{"origin_airport":"DEL","destination_airport":"BOM",'
               b'"price":100,"departure_at":"not-a-date"},'
               b'{"origin_airport":"DE","destination_airport":"BOM",'
               b'"price":100,"departure_at":"2026-10-15T07:00:00+05:30"}]}')
    assert TravelpayoutsCollector(token="x").parse(payload) == []


def test_travelpayouts_flags_a_changed_response():
    with pytest.raises(SchemaDrift):
        TravelpayoutsCollector(token="x").parse(b'{"results":[]}')
    with pytest.raises(SchemaDrift):
        TravelpayoutsCollector(token="x").parse(b'<html>maintenance</html>')


def test_travelpayouts_without_a_token_refuses_clearly():
    col = TravelpayoutsCollector(token=None)
    assert not col.configured
    with pytest.raises(CollectionRefused, match="TRAVELPAYOUTS_TOKEN"):
        col.search("DEL", "BOM", date(2026, 10, 15))


# ------------------------------------------------------------ lane A registry
def test_carrier_registry_records_measured_reachability():
    codes = carriers_by_code()
    assert {"6E", "AI", "QP", "SG"} <= set(codes)
    # every entry carries a measured status, not an assumption
    assert all(isinstance(c.observed_status, int) for c in CARRIERS)


def test_some_carriers_are_reachable_and_the_rest_are_recorded_as_not():
    reachable = reachable_carriers()
    assert reachable, "the registry should record at least one reachable carrier"
    unreachable = [c for c in CARRIERS if not c.reachable]
    # an unreachable carrier must say why rather than sit there unexplained
    assert all(c.note for c in unreachable)


def test_tariff_collector_refuses_an_unreachable_carrier():
    ai = carriers_by_code()["AI"]
    with pytest.raises(CollectionRefused, match="not reachable"):
        TariffCollector(ai).fetch_home()


def test_tariff_parsers_are_absent_not_faked():
    """A bespoke page needs a bespoke parser; a stub would be fiction."""
    qp = carriers_by_code()["QP"]
    with pytest.raises(NotImplementedError, match="No tariff parser"):
        TariffCollector(qp).parse(b"<html></html>")
