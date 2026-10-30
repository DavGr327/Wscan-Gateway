import time

from fake_gateway import PASSWORD, FakeData, FakeWeb

from wscan.cli import main
from wscan.codec import Frame


def write_cfg(tmp_path, web_port=80, data_port=20001):
    p = tmp_path / "config.toml"
    p.write_text(f"""
[gateway]
host = "127.0.0.1"
http_port = {web_port}
password_file = "{(tmp_path / 'auth').as_posix()}"

[[channel]]
name = "can1"
port = {data_port}
interface = "vcan0"
[channel.gateway]
packttime = 12
""")
    (tmp_path / "auth").write_text(f"admin:{PASSWORD}\n")
    (tmp_path / "auth").chmod(0o600)
    return str(p)


def test_config_command(tmp_path, capsys):
    assert main(["--config", write_cfg(tmp_path), "config"]) == 0
    assert "127.0.0.1:20001 <-> vcan0" in capsys.readouterr().out


def test_bad_config_exit_code(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[gateway]\nhost = 3\n")
    assert main(["--config", str(p), "config"]) == 2


def test_gw_apply_from_config(tmp_path, capsys):
    web = FakeWeb()
    try:
        cfg = write_cfg(tmp_path, web_port=web.port)
        assert main(["--config", cfg, "gw", "apply", "--dry-run"]) == 0
        assert "can1.packttime: 10 -> 12" in capsys.readouterr().out
        assert web.posts == []
        assert main(["--config", cfg, "gw", "apply", "--yes"]) == 0
        assert web.active["can1"]["pktt"] == "12" and web.restarts == 1
        assert main(["--config", cfg, "gw", "apply"]) == 0
        assert "already matches" in capsys.readouterr().out
    finally:
        web.close()


def test_dump_and_send_without_bridge(tmp_path, capsys):
    data = FakeData()
    cfg = write_cfg(tmp_path, data_port=data.port)
    assert main(["--config", cfg, "send", "123#DEADBEEF", "18FF50E5#01"]) == 0
    time.sleep(0.3)
    assert [f.can_id for f in data.received] == [0x123, 0x18FF50E5]
    data.send(Frame(0x101, b"\x01\x02"))  # piles up, delivered when dump connects
    assert main(["--config", cfg, "dump", "--seconds", "1"]) == 0
    assert "101  [2]  01 02" in capsys.readouterr().out
    data.close()
