# wscan

Use a **Waveshare 2-CH-CAN-TO-ETH** CAN-to-Ethernet gateway as if it were a CAN card in your
Linux machine (and, very likely, the other gateways of the USR-CANET200 family it is based on).

The gateway turns each CAN channel into a TCP port. wscan turns that port back into a normal
Linux CAN interface, so `candump`, `cansend`, Wireshark, python-can, CODESYS, ROS or anything
else that speaks SocketCAN works with a CAN bus that sits somewhere on the network.

```
CAN bus ── gateway 192.168.0.7 ──TCP 20001── wscan ── vcan0 ── candump / python-can / ...
          (CAN2) ────────────────TCP 20005── wscan ── vcan1
```

## Install

```bash
git clone https://github.com/DavGr327/Wscan-Gateway.git wscan
cd wscan
./install.sh
```

The installer asks a few questions; each has a default in brackets, Enter keeps it:

```
Gateway address [192.168.0.7]:
How is the gateway connected?
  1) to the same network as this device (switch / router)
  2) with a cable to a free Ethernet port of this device
choice [2]:
Ethernet port [eth0]:
This device's address on that port [192.168.0.10/24]:
Gateway web password (Enter = admin, the factory one):
Use CAN1? [Y/n]:
  CAN1: interface name on this device [vcan0]:
  CAN1: bus bit rate [1000000]:
Use CAN2? [Y/n]:
...
```

Then it sets up everything: this device's network address (if needed, with an automatic
rollback if the network breaks), the gateway's channel settings, the `wscan` command and a
service that keeps the bridge running across reboots. Nothing else to do: `candump vcan0`.

Needs Linux with Python ≥ 3.11 (nothing to install; on 3.9/3.10 the installer adds `tomli`)
and NetworkManager if the gateway is on a direct cable.

## Everyday use

| Command | |
|---|---|
| `wscan show` | everything at a glance: channels, bit rates, filters, traffic |
| `wscan set can1.bitrate=500000` | change a setting: it takes effect at once |
| `wscan save` / `wscan revert` | keep the change in the configuration / undo it |
| `wscan status` | is it working? |
| `wscan setup` | the installation questions again |

Settings for `wscan set`, on `can1` or `can2` (several at once are fine):

| Setting | Example | |
|---|---|---|
| `bitrate` | `can1.bitrate=500000` | bus bit rate, 10000-1000000. Every device on the bus needs the same |
| `mode` | `can1.mode=loopback` | `normal` or `loopback` |
| `filter` | `can1.filter=allow:100-1FF` | only these IDs (hex); `block:` drops them, `allow-ext:`/`block-ext:` for 29-bit IDs, `off` lets all through |
| `packet_ms`, `packet_frames` | `can1.packet_ms=5` | how often / after how many frames the gateway sends what it collected |
| `name` | `can1.name=can0` | interface name on this device (e.g. `can0` if CODESYS wants it) |
| `on` | `can2.on=false` | use the channel or not |
| `port` | `can1.port=21001` | TCP port, changed on the gateway and in the bridge |

```bash
wscan set can1.bitrate=500000 can1.filter=allow:100-1FF   # try it
candump vcan0                                              # does the bus still work?
wscan save                                                 # yes: keep it
wscan revert                                               # no: back to how it was
wscan set can2.on=false --save                             # sure already: set and keep in one go
```

`wscan -h` and `wscan set -h` list everything. `sudo` does not know the `wscan` command:
for the few commands that need it, run `sudo ./wscan ...` from the repo folder.

## Where to connect the gateway

**Same network as this device, through a switch** (the usual installation). Give the gateway
a fixed address of that network on its web page (*Network*), then answer `1` in the setup.

**Direct cable to a spare Ethernet port** (bench, or to keep the bus off the plant network).
Answer `2`: the setup gives that port an address on the gateway's subnet. A gateway fresh from
the factory is at 192.168.0.7; `sudo ./wscan discover --arp eth0` finds it even before the
port has an address.

Never put two interfaces of this device on the same subnet (e.g. Wi-Fi and Ethernet both on
192.168.1.x): both answer ARP for the device and TCP becomes intermittent while ping keeps
working. `wscan net check` warns about it and the setup refuses it.

**Security:** the gateway's data ports have no password. Anyone on the same network can send
frames onto the bus. Keep the gateway on a dedicated network or VLAN and change its default
`admin/admin`. The password never goes in the configuration file: the setup keeps it in
`~/.config/wscan/auth`, readable only by you.

## More commands

| Command | What it does |
|---|---|
| `wscan pending` | changes tried with `set` and not saved yet |
| `wscan discover [--arp IFACE]` | find gateways (`--arp` needs root) |
| `wscan net check` / `sudo ./wscan net apply` | how this device reaches the gateway / set its address |
| `wscan gw status` | gateway model, firmware, uptime |
| `wscan gw show [can1\|can2\|serial\|func\|net]` | every gateway setting, raw |
| `wscan gw apply` | make the gateway match the configuration file |
| `wscan gw backup [file]` / `wscan gw restart` | save every gateway setting / restart it |
| `wscan dump` / `wscan send 123#DEADBEEF` | talk to the gateway directly, no bridge (works on any OS) |
| `wscan config` | which configuration file is used, and what is in it |
| `wscan bridge` / `sudo ./wscan vcan-up` | what the service runs |

`wscan set` also takes the raw field names of `wscan gw show` (`can1.packttime=5`,
`serial.baud=9600`, `func.can2can=1`) and `gateway.host=...`.

## Configuration file

The setup and `wscan save` write `config.toml` for you, comments kept. To edit it by hand, see
[config.example.toml](config.example.toml): every key is optional. It is looked up at
`--config`, `$WSCAN_CONFIG`, `<repo>/config.toml`, `~/.config/wscan/config.toml`,
`/etc/wscan/config.toml`. After editing it: `wscan gw apply` for the gateway settings,
`sudo systemctl restart wscan-bridge` for the bridge ones.

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

## Performance

Measured with the [PMC simulator](examples/pmc-simulator) on a Raspberry Pi 5, 1 Mbit/s bus:

- round trip device → bus → device: median 13.9 ms, p95 22 ms (the gateway packs frames
  every 10 ms, `packet_ms`);
- 500 to 7000 frames/s (up to ~87 % bus load): no frame lost; bridge CPU < 1 %.

## Limitations

- No bus state: error frames, bus-off and error counters stay inside the gateway.
- `cansend` succeeds even when nothing on the bus acknowledges the frame.
- Classic CAN only (the gateway has no CAN FD).
- `ip link set vcan0 type can bitrate ...` does not apply: use `wscan set can1.bitrate=...`.
- The bridge needs the gateway in TCP server mode (the setup sets it); the gateway's TCP client
  and UDP modes are not supported yet.

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
