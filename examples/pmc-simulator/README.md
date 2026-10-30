# PMC CAN simulator

An [Arduino Portenta Machine Control](https://docs.arduino.cc/hardware/portenta-machine-control/)
that behaves like a CAN device, to test the gateway and the bridge with real frames.
Any CAN node works as well; this one also reports the CAN controller's error counters,
which tells wiring problems apart from configuration ones.

## Wiring

PMC `CAN H` / `CAN L` → gateway `CAN1 H` / `CAN1 L`. The gateway terminals are **L – G – H**,
ground in the middle. Turn on the gateway's 120 Ω termination (red `RES` dip switch, lever 1 = CAN1).
USB of the PMC → the Linux host (only for flashing and for the serial commands).

## Build and flash

```bash
arduino-cli core install arduino:mbed_portenta
arduino-cli lib install Arduino_PortentaMachineControl
./flash.sh
```

## What it sends

| ID | Rate | Content |
|---|---|---|
| `0x100` | 10 Hz | counter, sine ±1000 (10 s), millis() |
| `0x101` | 1 Hz | fake battery: 0.01 V, 0.1 A, SOC %, °C, counter |
| `0x18FF50E5` | 1 Hz | extended ID: counter, `CA FE BA BE` |
| `0x280` | on request | echo of every `0x200` it receives |
| `0x300` | `load N` | burst of N frames/s with a counter, to measure losses |

```bash
cansend vcan0 200#C0FFEE        # the PMC answers 280#C0FFEE
python3 pmc_cmd.py baud 500000  # bus bit rate
python3 pmc_cmd.py              # status: TEC/REC, ACTIVE/PASSIVE/BUS-OFF, last error
```

Reading the error state: `LEC=ACK` alone on the bus is normal (nobody acknowledges);
`LEC=BIT0` with `BUS-OFF` only when the cable is attached means the wiring squashes the
signal (short, wire on G, H and L in the same terminal).
