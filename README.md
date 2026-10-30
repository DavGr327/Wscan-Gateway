# wscan

SocketCAN bridge and command-line tools for the **Waveshare 2-CH-CAN-TO-ETH** CAN-to-Ethernet
gateway (and, very likely, the other gateways of the USR-CANET200 family it is based on).

The gateway turns each CAN channel into a TCP port. wscan turns that TCP port back into a
normal Linux CAN interface, so `candump`, `cansend`, Wireshark, python-can, CODESYS, ROS or
anything else that speaks SocketCAN works with a CAN bus that sits somewhere on the network.

```
CAN bus ── gateway 192.168.0.7 ──TCP 20001── wscan bridge ── vcan0 ── candump / python-can / ...
          (CAN2) ────────────────TCP 20005── wscan bridge ── vcan1
```

- **Clone and run**: Python ≥ 3.11 standard library only, nothing to install (3.9/3.10 need `tomli`).
- **One config file** for everything: gateway address, channels, interface names, this host's network.
- **Finds the gateway** on the network, even on a subnet the host has no address on.
- **Configures the gateway** from the command line, and keeps it matching the config file.
- **Survives** gateway restarts, unplugged cables and host reboots (systemd service).

## Quick start

```bash
git clone https://github.com/DavGr327/Wscan-Gateway.git wscan && cd wscan
./install.sh                 # config.toml from the example, `wscan` on the PATH, bridge service
wscan discover               # where is the gateway?
nano config.toml             # gateway host, channels (and [network] if needed, see below)
sudo systemctl restart wscan-bridge
wscan status
candump vcan0
```

`./wscan <command>` works straight from the clone too, without `install.sh`.
`./install.sh --no-service` skips the systemd unit; `./uninstall.sh` removes it.

## Two ways to connect the gateway

**Same network as the host, through a switch** (the usual installation). Give the gateway a
static address of that network on its web page (*Network*), put it in `[gateway] host`, done.

**Direct cable to a spare Ethernet port** (bench, or to keep the bus off the plant network).
The port needs an address on the gateway's subnet. Put it in the config and let wscan set it
through NetworkManager, with an automatic rollback if the host loses its default route:

```toml
[network]
interface = "eth0"
address = "192.168.0.10/24"
```
```bash
sudo wscan discover --arp eth0      # finds the gateway even before eth0 has an address
sudo wscan net apply
wscan net check
```

Never put two interfaces of the host on the same subnet (e.g. Wi-Fi and Ethernet both on
192.168.1.x): both answer ARP for the host and TCP becomes intermittent while ping keeps
working. `wscan net check` warns about it, `wscan net apply` refuses to do it.

## Configuration

Everything lives in `config.toml` (see [config.example.toml](config.example.toml), every key
is optional). It is looked up at `--config`, `$WSCAN_CONFIG`, `<repo>/config.toml`,
`~/.config/wscan/config.toml`, `/etc/wscan/config.toml`; `wscan config` shows which one is used.

```toml
[gateway]
host = "192.168.0.7"

[[channel]]
name = "can1"          # gateway channel
port = 20001           # its TCP port on the gateway
interface = "vcan0"    # SocketCAN interface on this host (can0 if CODESYS wants that name)

[channel.gateway]      # optional: settings `wscan gw apply` writes to the gateway
baud = 500000
```

The gateway password never goes in the config: `wscan gw ...` reads it from
`$WSCAN_PASSWORD`, from `password_file` (`~/.config/wscan/auth`, one line `user:password`,
`chmod 600`), or asks for it. The bridge itself does not need it.

## Commands

| Command | What it does |
|---|---|
| `wscan status` | service, channels (up/down, frames, reconnects), gateway reachability |
| `wscan discover [--arp IFACE]` | find gateways (`--arp` needs root) |
| `wscan net check` / `sudo wscan net apply` | how this host reaches the gateway / set `[network]` |
| `wscan gw status` | model, firmware, uptime |
| `wscan gw show [can1\|can2\|serial\|func\|net]` | current gateway settings, decoded |
| `wscan gw set can1 baud=500000 --restart` | change settings (`--dry-run` to preview) |
| `wscan gw apply` | make the gateway match `config.toml`, then restart it |
| `wscan gw backup [file]` / `wscan gw restart` | save every setting / restart |
| `wscan dump` / `wscan send 123#DEADBEEF` | talk to the gateway directly, no bridge (any OS) |
| `wscan bridge` / `sudo wscan vcan-up` | what the service runs |

### Changing the bus bit rate

Every node on the bus must use the same bit rate: change the gateway *and* the devices.

```toml
[channel.gateway]
baud = 500000
```
```bash
wscan gw apply    # writes, verifies, restarts the gateway; the bridge reconnects by itself
```

`ip link set vcan0 type can bitrate ...` does not apply here: a virtual interface has no bit rate.

## Things the gateway firmware does (V1.1.3)

Found the hard way, handled by wscan:

- Each settings form posts to an address with a section query (`can1set.cgi?ser2net2.@ser2net[0]`).
  Posted without it, the gateway **reboots and saves nothing**.
- Saved settings take effect **only after a restart** (~3 s, the link goes down for ~2 s).
- While no client is connected, the gateway **buffers frames** and delivers them on connect:
  the bridge drops that backlog (`flush_seconds`).
- Several TCP clients can connect to the same port; each gets its own copy of the frames.
  Frames a client sends are not echoed back.
- A rebooting gateway does not close connections: the bridge notices through TCP keepalive.
- The data ports have **no authentication**: anyone on the same network can send frames to the
  bus. Keep the gateway on a dedicated network or VLAN and change the default `admin/admin`.

## Performance

Measured with the [PMC simulator](examples/pmc-simulator) on a Raspberry Pi 5, 1 Mbit/s bus:

- round trip host → bus → device → bus → host: median 13.9 ms, p95 22 ms (the gateway packs
  frames every 10 ms, `packttime`);
- 500 to 7000 frames/s (up to ~87 % bus load): no frame lost; bridge CPU < 1 %.

## Limitations

- No bus state: error frames, bus-off and error counters stay inside the gateway.
- `cansend` succeeds even when nothing on the bus acknowledges the frame.
- Classic CAN only (the gateway has no CAN FD).
- The bridge connects to the gateway in TCP server mode (`gw apply` sets it); the gateway's
  TCP client and UDP modes are not supported yet.

## Development

```bash
pip install -e ".[test]"
pytest
```

The tests run against a fake gateway (`tests/fake_gateway.py`) that reproduces the firmware
behaviour above, so no hardware is needed. The bridge test also needs a virtual CAN interface:

```bash
sudo modprobe vcan && sudo ip link add dev vcantest type vcan && sudo ip link set vcantest up
WSCAN_TEST_VCAN=vcantest pytest
```

## Related projects

If you only need the frames inside a Python program, these libraries talk to the same
13-byte protocol directly: python-can's `usr_canet` interface, `can-waveshare`, `caneth`.
wscan is for when the gateway should look like a CAN interface to the whole system.

## License

[MIT](LICENSE)
