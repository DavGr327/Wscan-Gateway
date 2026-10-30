import json

import pytest
from fake_gateway import PASSWORD, FakeWeb

from wscan.cli import main
from wscan.config import REPO_ROOT, load


@pytest.fixture
def setup(tmp_path):
    web = FakeWeb()
    cfg = tmp_path / "config.toml"
    text = (REPO_ROOT / "config.example.toml").read_text()
    text = text.replace('host = "192.168.0.7"', 'host = "127.0.0.1"')
    text = text.replace("http_port = 80", f"http_port = {web.port}")
    text = text.replace('password_file = "~/.config/wscan/auth"',
                        f'password_file = "{(tmp_path / "auth").as_posix()}"')
    cfg.write_text(text)
    (tmp_path / "auth").write_text(f"admin:{PASSWORD}\n")
    yield web, cfg
    web.close()


def run(cfg, *args):
    return main(["--config", str(cfg), *args])


def test_set_then_save(setup, capsys):
    web, cfg = setup
    before = cfg.read_text()
    assert run(cfg, "set", "can1.baud=500000", "can1.packttime=5") == 0
    assert web.active["can1"]["C0baud"] == "500000" and web.restarts == 1  # live now
    assert cfg.read_text() == before  # config untouched
    pending = json.loads((cfg.parent / "config.pending.json").read_text())
    assert pending["gateway"]["can1"]["baud"] == ["1000000", "500000"]

    run(cfg, "pending")
    assert "can1.bitrate: 1000000 -> 500000" in capsys.readouterr().out
    assert run(cfg, "gw", "apply") == 1  # refuses while there are unsaved changes

    assert run(cfg, "save") == 0
    assert not (cfg.parent / "config.pending.json").exists()
    assert (cfg.parent / "config.toml.bak").read_text() == before
    c = load(str(cfg))
    assert c.channel("can1").gateway == {"baud": "500000", "packttime": "5"}
    assert "# One block per gateway channel to bridge." in cfg.read_text()
    capsys.readouterr()
    assert run(cfg, "gw", "apply") == 0
    assert "already matches" in capsys.readouterr().out


def test_set_then_revert(setup):
    web, cfg = setup
    before = cfg.read_text()
    run(cfg, "set", "can2.packttime=7")
    run(cfg, "set", "can2.packttime=8")  # second try: revert must go back to the first value
    assert web.active["can2"]["pktt"] == "8"
    assert run(cfg, "revert") == 0
    assert web.active["can2"]["pktt"] == "10"
    assert cfg.read_text() == before
    assert not (cfg.parent / "config.pending.json").exists()


def test_bridge_settings_and_port(setup):
    web, cfg = setup
    assert run(cfg, "set", "can1.port=21001", "can1.interface=can0") == 0
    assert web.active["can1"]["localport"] == "21001"  # the gateway follows the port
    live = load(str(cfg))
    assert live.channel("can1").port == 21001 and live.channel("can1").interface == "can0"
    assert load(str(cfg), with_pending=False).channel("can1").port == 20001  # file unchanged
    assert run(cfg, "save") == 0
    saved = load(str(cfg))
    assert saved.channel("can1").port == 21001 and saved.channel("can1").interface == "can0"
    assert saved.unsaved == 0


def test_set_back_to_original_clears_pending(setup):
    web, cfg = setup
    run(cfg, "set", "can1.packttime=9")
    run(cfg, "set", "can1.packttime=10")
    assert not (cfg.parent / "config.pending.json").exists()


@pytest.mark.parametrize("arg", ["can1.bauds=1", "can3.baud=1", "nodot=1", "can1.port=abc",
                                 "can1.interface=waytoolonginterfacename", "can1.bitrate=5",
                                 "can1.mode=fast", "can1.filter=allow:800", "can1.filter=maybe"])
def test_bad_set_changes_nothing(setup, arg):
    web, cfg = setup
    assert run(cfg, "set", arg) == 1
    assert web.posts == [] and not (cfg.parent / "config.pending.json").exists()


def test_plain_names(setup):
    web, cfg = setup
    assert run(cfg, "set", "can1.bitrate=250000", "can1.mode=loopback", "can1.filter=allow:100-1FF",
               "can1.packet_ms=5", "can2.name=motor", "can2.on=false", "--save") == 0
    can1 = web.active["can1"]
    assert (can1["C0baud"], can1["C0mode"], can1["pktt"]) == ("250000", "1", "5")
    assert (can1["fittype"], can1["StdIDLow"], can1["StdIDHigh"]) == ("1", "256", "511")
    c = load(str(cfg))
    assert c.unsaved == 0  # --save
    assert c.channel("can2").interface == "motor" and not c.channel("can2").enabled
    assert c.channel("can1").gateway["baud"] == "250000"


def test_show(setup, capsys):
    web, cfg = setup
    run(cfg, "set", "can1.filter=block-ext:18FF0000-18FFFFFF")
    capsys.readouterr()
    assert run(cfg, "show") == 0
    out = capsys.readouterr().out
    assert "2-CH-CAN-TO-ETH firmware V1.1.3" in out
    assert "bitrate 1000000   mode normal   filter block-ext:18FF0000-18FFFFFF" in out
    assert "not saved" in out
