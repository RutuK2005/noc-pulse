import copy
from pathlib import Path

import pytest

from noc_pulse.inventory import InventoryError, is_external, load_inventory, parse_inventory

from conftest import LAB

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_inventory_is_valid():
    inv = load_inventory(ROOT / "config" / "inventory.yaml")
    assert "site-gw" in inv.targets
    assert inv.ancestors("web-tatacomm") == ["dns-site", "site-gw"]
    for t in inv.targets.values():
        for p in t.probes:
            assert p.interval_s >= 30, f"{t.id}/{p.kind} probes too often"


def test_graph_helpers(inv):
    assert inv.ancestors("app") == ["dns", "gw"]
    assert set(inv.descendants("gw")) == {"dns", "app", "api"}
    assert {s.id for s in inv.impacted_services("dns")} == {"web"}
    assert inv.impact_of("gw") == "high"
    assert inv.impact_of("api") == "low"


def test_cycle_detected():
    data = copy.deepcopy(LAB)
    data["targets"][0]["depends_on"] = ["app"]
    with pytest.raises(InventoryError, match="cycle"):
        parse_inventory(data)


def test_unknown_dependency_rejected():
    data = copy.deepcopy(LAB)
    data["targets"][1]["depends_on"] = ["nope"]
    with pytest.raises(InventoryError, match="unknown"):
        parse_inventory(data)


def test_politeness_floor_for_external_targets():
    data = copy.deepcopy(LAB)
    data["targets"][2]["probes"][0]["interval_s"] = 5  # app.example is external
    with pytest.raises(InventoryError, match="politeness"):
        parse_inventory(data)
    data["targets"][0]["probes"][0]["interval_s"] = 5  # private gateway: allowed
    data["targets"][2]["probes"][0]["interval_s"] = 60
    parse_inventory(data)


@pytest.mark.parametrize(
    "host,external",
    [
        ("1.1.1.1", True),
        ("www.example.com", True),
        ("192.168.1.1", False),
        ("127.0.0.1", False),
        ("auto-gateway", False),
        ("system", False),
    ],
)
def test_is_external(host, external):
    assert is_external(host) is external
