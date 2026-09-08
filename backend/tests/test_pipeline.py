"""
The pipeline and the data layer, end to end on a temporary database.

These are the tests that would have caught the two integration bugs found
while wiring this up: an item identity that collapsed different routes onto
each other, and weights keyed differently from fares so nothing ever joined.
"""
from datetime import date, datetime, timezone

import pytest

from vimaan import db, pipeline
from vimaan.data.airports import iata, route_key
from vimaan.models import Cabin, FareObservation, IndexPoint, Lane, RouteWeight, lead_bucket


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def fare(route_o, route_d, depart, bucket, flight, price, month_shift=0):
    from datetime import timedelta
    d = depart
    return FareObservation(
        lane=Lane.LICENSED, source="test",
        collected_at=datetime.combine(d - timedelta(days=bucket),
                                      datetime.min.time(), tzinfo=timezone.utc),
        origin=route_o, destination=route_d, depart_date=d,
        carrier="6E", flight_no=f"6E-{flight}", cabin=Cabin.ECONOMY,
        price_total=price,
    )


# ------------------------------------------------------------------ models
def test_route_id_is_direction_independent():
    a = fare("DEL", "BOM", date(2026, 3, 15), 7, 100, 5000)
    b = fare("BOM", "DEL", date(2026, 3, 15), 7, 100, 5000)
    assert a.route == b.route == "BOM-DEL"


def test_item_identity_separates_routes():
    """The bug this catches: one flight number reused on two sectors.

    Without the route in the identity these two collapse into one item and the
    matched sample silently loses a route.
    """
    a = fare("DEL", "BOM", date(2026, 3, 15), 7, 100, 5000)
    b = fare("BLR", "MAA", date(2026, 3, 15), 7, 100, 5000)
    assert a.item_id != b.item_id


def test_strata_never_mix_lead_buckets():
    a = fare("DEL", "BOM", date(2026, 3, 15), 1, 100, 9000)
    b = fare("DEL", "BOM", date(2026, 3, 15), 60, 100, 4000)
    assert a.stratum != b.stratum


@pytest.mark.parametrize("days,expected", [
    (0, 1), (1, 1), (2, 3), (5, 7), (10, 7), (12, 14), (40, 45), (90, 60),
])
def test_lead_times_snap_to_fixed_buckets(days, expected):
    assert lead_bucket(days) == expected


def test_all_inclusive_price_is_required_positive():
    with pytest.raises(Exception):
        fare("DEL", "BOM", date(2026, 3, 15), 7, 100, -1)


# --------------------------------------------------------------- airports
def test_city_names_map_to_iata():
    assert iata("DELHI") == "DEL"
    assert iata("  bengaluru ") == "BLR"
    assert iata("NOWHERE") is None


def test_route_key_is_iata_and_sorted():
    assert route_key("MUMBAI", "DELHI") == "BOM-DEL"
    assert route_key("DELHI", "MUMBAI") == "BOM-DEL"


def test_unmapped_city_yields_no_route_key():
    """Better a gap than a wrong passenger weight on a real route."""
    assert route_key("DELHI", "ATLANTIS") is None


# --------------------------------------------------------------------- db
def test_snapshots_are_content_addressed(conn):
    a, d1 = db.put_snapshot(conn, lane="B", source="t", payload=b"same bytes")
    b, d2 = db.put_snapshot(conn, lane="B", source="t", payload=b"same bytes")
    assert a == b and d1 == d2
    assert db.stats(conn)["snapshots"] == 1


def test_refetching_does_not_duplicate_fares(conn):
    f = [fare("DEL", "BOM", date(2026, 3, 15), 7, 100, 5000)]
    assert db.put_fares(conn, f) == 1
    assert db.put_fares(conn, f) == 0
    assert db.stats(conn)["fares"] == 1


def test_weights_round_trip(conn):
    db.put_weights(conn, [
        RouteWeight(route="BOM-DEL", passengers=6e6, share=0.6, base_year=2024),
        RouteWeight(route="BLR-DEL", passengers=4e6, share=0.4, base_year=2024),
    ])
    w = db.get_weights(conn, 2024)
    assert w == pytest.approx({"BOM-DEL": 0.6, "BLR-DEL": 0.4})


# --------------------------------------------------------------- pipeline
def _panel(conn, months=6, routes=(("DEL", "BOM"), ("BLR", "MAA"))):
    db.put_weights(conn, [
        RouteWeight(route="BOM-DEL", passengers=6e6, share=0.6, base_year=2024),
        RouteWeight(route="BLR-MAA", passengers=4e6, share=0.4, base_year=2024),
    ])
    rows = []
    for m in range(months):
        depart = date(2026, 1 + m, 15)
        for (o, d) in routes:
            for bucket in (7, 30):
                for flight in range(3):
                    rows.append(fare(o, d, depart, bucket, 100 + flight,
                                     5000 * (1.01 ** m) + flight * 30))
    db.put_fares(conn, rows)


def test_pipeline_publishes_a_figure_with_provenance(conn):
    _panel(conn)
    out = pipeline.run_all(conn)
    assert out["published_periods"] > 0
    latest = out["latest"]
    assert latest["repro_hash"]
    assert 0 < latest["coverage_pct"] <= 100
    assert latest["route_count"] == 2


def test_full_weight_coverage_is_not_flagged_provisional(conn):
    _panel(conn)
    pipeline.run_all(conn)
    series = db.get_series(conn)
    # both weighted routes are priced, so coverage is complete
    assert series[-1]["coverage_pct"] == pytest.approx(100.0)
    assert not series[-1]["provisional"]


def test_missing_routes_lower_coverage_and_flag_provisional(conn):
    """A route we never priced must reduce coverage, not vanish quietly."""
    _panel(conn)
    db.put_weights(conn, [RouteWeight(route="CCU-DEL", passengers=9e6,
                                      share=0.9, base_year=2024)])
    pipeline.run_all(conn)
    latest = db.get_series(conn)[-1]
    assert latest["coverage_pct"] < 100.0
    assert latest["provisional"]


def test_surface_uses_the_median_not_the_mean(conn):
    """One absurd quote must not move a cell."""
    db.put_weights(conn, [RouteWeight(route="BOM-DEL", passengers=1e6,
                                      share=1.0, base_year=2024)])
    depart = date(2026, 5, 15)
    rows = [fare("DEL", "BOM", depart, 7, i, p)
            for i, p in enumerate([5000, 5100, 5200, 999999])]
    db.put_fares(conn, rows)
    pipeline.build_surface(conn)
    cell = conn.execute(
        "SELECT median_fare, n_obs FROM gold_surface WHERE lead_bucket = 7"
    ).fetchone()
    assert cell["n_obs"] == 4
    assert cell["median_fare"] < 6000        # the outlier did not drag it


def test_contributions_reconcile_with_the_published_value(conn):
    _panel(conn)
    pipeline.run_all(conn)
    period = db.get_series(conn)[-1]["period"]
    value = db.get_series(conn)[-1]["value"]
    c = pipeline.route_contributions(conn, period)
    assert sum(c.values()) == pytest.approx(value - 100.0, abs=1e-6)


def test_publishing_without_weights_is_an_error(conn):
    db.put_fares(conn, [fare("DEL", "BOM", date(2026, 3, 15), 7, 1, 5000)])
    pipeline.build_surface(conn)
    with pytest.raises(ValueError, match="no route weights"):
        pipeline.publish(conn)


# ------------------------------------------------------------ index point
def test_repro_hash_changes_with_the_inputs():
    a = IndexPoint(period="2026-07", value=104.1, coverage_pct=90, imputed_pct=0)
    h1 = a.compute_hash("value=104.1")
    h2 = a.compute_hash("value=104.2")
    assert h1 != h2
    assert h1 == a.compute_hash("value=104.1")     # and is stable
