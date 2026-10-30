import pytest
from fake_gateway import PASSWORD, FakeWeb

from wscan.config import load
from wscan.wizard import Prompter, SetupError, _suggest_address, run


class Script:
    """Feeds the wizard's questions from a list; records what it printed."""

    def __init__(self, answers, secrets=()):
        self.answers, self.secrets, self.out = list(answers), list(secrets), []

    def ask(self, question):
        self.out.append(question)
        return self.answers.pop(0)

    def secret(self, question):
        self.out.append(question)
        return self.secrets.pop(0)

    def say(self, text=""):
        self.out.append(text)

    def prompter(self):
        return Prompter(self.ask, self.secret, self.say)


@pytest.fixture
def web():
    w = FakeWeb()
    yield w
    w.close()


def base_config(tmp_path, web):
    p = tmp_path / "config.toml"
    p.write_text(f'[gateway]\nhost = "127.0.0.1"\nhttp_port = {web.port}\n'
                 f'password_file = "{(tmp_path / "auth").as_posix()}"\n')
    return p


def test_defaults_then_changes(tmp_path, web):
    path = base_config(tmp_path, web)
    s = Script([
        "",            # gateway address: keep 127.0.0.1 (reachable)
        "1",           # same network
        "",            # use CAN1: yes
        "can0",        # CAN1 interface name
        "500000",      # CAN1 bit rate
        "n",           # CAN2 not used
        "",            # save and apply: yes
    ], secrets=[PASSWORD])
    net_calls = []
    assert run(load(str(path)), s.prompter(), discover=lambda: [], net_apply=net_calls.append) == 0

    cfg = load(str(path))
    assert cfg.channel("can1").interface == "can0" and cfg.channel("can1").gateway == {"baud": "500000"}
    assert not cfg.channel("can2").enabled
    assert not cfg.network.address and net_calls == []
    assert (tmp_path / "auth").read_text().strip() == f"admin:{PASSWORD}"
    assert web.active["can1"]["C0baud"] == "500000" and web.restarts == 1  # written into the gateway
    assert any("wscan show" in line for line in s.out)


def test_cable_mode_sets_network_and_runs_net_apply(tmp_path, web):
    path = base_config(tmp_path, web)
    (tmp_path / "auth").write_text(f"admin:{PASSWORD}\n")
    s = Script([
        "", "2",                  # address kept, direct cable
        "eth9", "",               # port, suggested address for this device
        "",                       # keep the saved password
        "", "", "",               # CAN1: yes, vcan0, 1000000
        "", "", "",               # CAN2: yes, vcan1, 1000000
        "",                       # save and apply
    ])
    net_calls = []
    assert run(load(str(path)), s.prompter(), discover=lambda: [], net_apply=lambda p: net_calls.append(p) or 0) == 0
    cfg = load(str(path))
    assert (cfg.network.interface, cfg.network.address) == ("eth9", "127.0.0.10/24")
    assert net_calls == [path]


def test_bad_answers_are_asked_again(tmp_path, web):
    path = base_config(tmp_path, web)
    s = Script(["not-an-ip", "", "1", "", "toolonginterfacename0", "vcan0", "7", "1000000",
                "", "vcan0", "vcan1", "", "n"], secrets=[""])
    assert run(load(str(path)), s.prompter(), discover=lambda: [], net_apply=lambda p: 0) == 1  # answered no
    assert "already used by the other channel" in " ".join(s.out)
    assert sum("between 10000 and 1000000" in line for line in s.out) == 1
    assert load(str(path)).channel("can1").interface == "vcan0"  # nothing written


def test_refuses_with_unsaved_changes(tmp_path, web):
    path = base_config(tmp_path, web)
    (tmp_path / "config.pending.json").write_text('{"gateway": {}, "bridge": {"can1.interface": ["vcan0", "x"]}}')
    with pytest.raises(SetupError, match="not saved"):
        run(load(str(path)), Script([]).prompter())


def test_suggested_address_avoids_the_gateway():
    assert _suggest_address("192.168.0.7") == "192.168.0.10/24"
    assert _suggest_address("10.1.2.10") == "10.1.2.11/24"
