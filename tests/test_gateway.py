import pytest
from fake_gateway import PASSWORD, USER, FakeWeb

from wscan.config import Channel, Config
from wscan.gateway import Gateway, GatewayError, parse_vars, wanted_from_config


@pytest.fixture
def fake():
    f = FakeWeb()
    yield f
    f.close()


@pytest.fixture
def gw(fake):
    return Gateway("127.0.0.1", USER, PASSWORD, http_port=fake.port, timeout=2, reboot_wait=10)


def test_parse_vars_keeps_quoted_spaces():
    v = parse_vars("var a = 1;\nvar b = 'x y ';\nvar f = document.form;\nvar a = 2;")
    assert v == {"a": "1", "b": "x y "}


def test_wrong_password(fake):
    bad = Gateway("127.0.0.1", USER, "nope", http_port=fake.port, timeout=2)
    with pytest.raises(GatewayError, match="401"):
        bad.status()


def test_read(gw):
    assert gw.status()["revision"] == "V1.1.3"
    can2 = gw.read("can2")
    assert can2["localport"] == "20005" and can2["baud"] == "1000000"
    assert gw.read("net")["ipaddr"] == "192.168.0.7"
    assert set(gw.backup()) == {"status", "can1", "can2", "serial", "func", "net"}


def test_write_uses_the_section_query_and_takes_effect_after_restart(gw, fake):
    gw.write("can1", {"baud": "500000"})
    assert fake.posts == ["can0set.cgi?ser2net2.@ser2net[0]"]
    assert fake.reboots == 0
    assert fake.saved["can1"]["C0baud"] == "500000"
    assert fake.active["can1"]["C0baud"] == "1000000"  # saved, not active yet
    gw.restart()
    assert fake.active["can1"]["C0baud"] == "500000"
    assert fake.posts[-1] == "rebootset.cgi?~reboot -d 2 -f"


def test_unknown_field_and_read_only_page(gw, fake):
    with pytest.raises(GatewayError, match="unknown field"):
        gw.write("can1", {"bauds": "1"})
    with pytest.raises(GatewayError, match="read-only"):
        gw.write("net", {"ipaddr": "1.2.3.4"})
    assert fake.posts == []


def test_whole_form_is_sent_so_other_fields_survive(gw, fake):
    gw.write("can2", {"packttime": "11"})
    assert fake.saved["can2"]["pktt"] == "11"
    assert fake.saved["can2"]["heartpkt"] == "88 12 34 56 78 01 02 03 04 05 06 07 08 "
    assert fake.saved["can2"]["localport"] == "20005"


def test_apply_writes_only_differences_and_restarts_once(gw, fake):
    cfg = Config(channels=[Channel("can1", 20001, "vcan0", gateway={"baud": "250000"}),
                           Channel("can2", 20009, "vcan1")])
    plan = gw.plan(wanted_from_config(cfg))
    assert plan == {"can1": {"baud": ("1000000", "250000")}, "can2": {"localport": ("20005", "20009")}}
    gw.apply(wanted_from_config(cfg))
    assert fake.restarts == 1
    assert fake.active["can1"]["C0baud"] == "250000" and fake.active["can2"]["localport"] == "20009"
    assert gw.plan(wanted_from_config(cfg)) == {}


def test_apply_forces_tcp_server_mode(gw, fake):
    fake.saved["can1"]["workmode"] = "2"
    cfg = Config(channels=[Channel("can1", 20001, "vcan0")])
    assert gw.plan(wanted_from_config(cfg)) == {"can1": {"workmode": ("2", "1")}}
