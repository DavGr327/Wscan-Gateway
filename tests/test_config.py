import pytest

from wscan.config import REPO_ROOT, ConfigError, load


def write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return str(p)


def test_example_config_is_valid():
    cfg = load(str(REPO_ROOT / "config.example.toml"))
    assert cfg.host == "192.168.0.7"
    assert [(c.name, c.port, c.interface) for c in cfg.channels] == [("can1", 20001, "vcan0"),
                                                                      ("can2", 20005, "vcan1")]


def test_defaults_without_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WSCAN_CONFIG", raising=False)
    monkeypatch.setattr("wscan.config.SEARCH_PATHS", (tmp_path / "missing.toml",))
    cfg = load()
    assert cfg.source is None and len(cfg.enabled_channels) == 2


def test_env_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("WSCAN_CONFIG", write(tmp_path, '[gateway]\nhost = "10.0.0.5"\n'))
    assert load().host == "10.0.0.5"


def test_channels_and_gateway_settings(tmp_path):
    cfg = load(write(tmp_path, """
[[channel]]
name = "can1"
port = 21000
interface = "can0"
[channel.gateway]
baud = 500000

[[channel]]
name = "can2"
port = 21001
interface = "can1"
enabled = false
"""))
    assert cfg.channels[0].gateway == {"baud": "500000"}
    assert [c.name for c in cfg.enabled_channels] == ["can1"]


@pytest.mark.parametrize("text, msg", [
    ('[gateway]\nhots = "x"\n', "unknown key"),
    ('[gatway]\n', "unknown section"),
    ('[gateway]\nhttp_port = "80"\n', "must be int"),
    ('[[channel]]\nname = "can3"\nport = 1\ninterface = "vcan0"\n', "must be one of"),
    ('[[channel]]\nname = "can1"\nport = 1\ninterface = "vcan0"\n'
     '[[channel]]\nname = "can2"\nport = 2\ninterface = "vcan0"\n', "used twice"),
    ('[[channel]]\nname = "can1"\ninterface = "vcan0"\n', "missing"),
    ('[network]\ninterface = "eth0"\n', "both"),
    ('[network]\ninterface = "eth0"\naddress = "192.168.0.10"\n', "prefix length"),
])
def test_mistakes_are_reported(tmp_path, text, msg):
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, text))


def test_missing_explicit_file():
    with pytest.raises(ConfigError, match="not found"):
        load("/no/such/wscan.toml")
