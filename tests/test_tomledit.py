from wscan import tomledit
from wscan.config import REPO_ROOT, load

EXAMPLE = (REPO_ROOT / "config.example.toml").read_text()


def live_lines(text, start):
    """Uncommented lines starting with `start` (the example also has commented-out ones)."""
    return sum(1 for line in text.splitlines() if line.lstrip().startswith(start))


def edited(tmp_path, text, *edits):
    lines = text.splitlines()
    for fn, *args in edits:
        fn(lines, *args)
    p = tmp_path / "config.toml"
    p.write_text("\n".join(lines) + "\n")
    return p, load(str(p), with_pending=False)


def test_channel_keys_replace_and_comments_survive(tmp_path):
    p, cfg = edited(tmp_path, EXAMPLE,
                    (tomledit.set_channel_key, "can1", "interface", "can0"),
                    (tomledit.set_channel_key, "can2", "enabled", False),
                    (tomledit.set_channel_key, "can2", "port", 21005))
    assert cfg.channel("can1").interface == "can0"
    assert cfg.channel("can2").enabled is False and cfg.channel("can2").port == 21005
    text = p.read_text()
    assert "# One block per gateway channel to bridge." in text
    assert live_lines(text, "interface =") == 2


def test_channel_gateway_table_created_then_updated(tmp_path):
    p, cfg = edited(tmp_path, EXAMPLE,
                    (tomledit.set_channel_gateway, "can1", "baud", "500000"),
                    (tomledit.set_channel_gateway, "can2", "packttime", "5"),
                    (tomledit.set_channel_gateway, "can1", "baud", "250000"),
                    (tomledit.set_channel_gateway, "can1", "heartpkt", "88 12 34 "))
    assert cfg.channel("can1").gateway == {"baud": "250000", "heartpkt": "88 12 34 "}
    assert cfg.channel("can2").gateway == {"packttime": "5"}
    assert live_lines(p.read_text(), "[channel.gateway]") == 2


def test_sections(tmp_path):
    _, cfg = edited(tmp_path, EXAMPLE,
                    (tomledit.set_section_key, "gateway", "host", "10.0.0.9"),
                    (tomledit.set_section_key, "gateway.serial", "baud", "9600"),
                    (tomledit.set_section_key, "gateway.func", "can2can", "1"))
    assert cfg.host == "10.0.0.9"
    assert cfg.gateway_pages == {"serial": {"baud": "9600"}, "func": {"can2can": "1"}}


def test_file_without_channels(tmp_path):
    lines = ['[gateway]', 'host = "192.168.0.7"']
    assert not tomledit.has_channels(lines)
    tomledit.add_channel(lines, "can1", 20001, "vcan0")
    tomledit.add_channel(lines, "can2", 20005, "vcan1", enabled=False)
    tomledit.set_channel_gateway(lines, "can1", "baud", "500000")
    p = tmp_path / "c.toml"
    p.write_text("\n".join(lines) + "\n")
    cfg = load(str(p), with_pending=False)
    assert [c.name for c in cfg.enabled_channels] == ["can1"]
    assert cfg.channel("can1").gateway == {"baud": "500000"}
