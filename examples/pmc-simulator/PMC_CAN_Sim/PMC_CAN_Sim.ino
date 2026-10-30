/*
 * PMC_CAN_Sim - an Arduino Portenta Machine Control that pretends to be a CAN device,
 * to test the gateway and the wscan bridge end to end.
 *
 * Bus at 1 Mbit/s like a factory-default gateway. Multi-byte values are big-endian.
 *
 * TX
 *   0x100       10 Hz  [0-1] counter  [2-3] sine int16 +/-1000, 10 s period  [4-7] millis()
 *   0x101        1 Hz  fake battery: [0-1] voltage 0.01 V  [2-3] current int16 0.1 A
 *                      [4] SOC %  [5] temperature degC  [6-7] counter
 *   0x18FF50E5   1 Hz  extended ID: [0-3] counter  [4-7] 0xCAFEBABE
 * RX
 *   0x200       answered on 0x280 with the same data (round-trip test)
 *   everything received is printed on the USB serial port
 *
 * Commands on the USB serial port, one per line:
 *   baud 125000|250000|500000|1000000   change the bus bit rate
 *   load <frames/s>                     burst of 0x300: [0-3] counter [4-7] micros(); 0 = stop
 *
 * Status line once a second, with the CAN controller's error state:
 *   tx fast=.. slow=.. err=.. rx=.. baud=.. | TEC=.. REC=.. ACTIVE|WARN|PASSIVE|BUS-OFF LEC=..
 */

#include <Arduino_PortentaMachineControl.h>

static uint32_t const ID_FAST = 0x100;
static uint32_t const ID_BATT = 0x101;
static uint32_t const ID_EXT  = 0x18FF50E5;
static uint32_t const ID_PING = 0x200;
static uint32_t const ID_PONG = 0x280;
static uint32_t const ID_LOAD = 0x300;

static uint32_t cnt_fast = 0, cnt_slow = 0, tx_err = 0, rx_cnt = 0;
static uint32_t t_fast = 0, t_slow = 0;
static uint32_t load_fps = 0, load_cnt = 0, load_t0 = 0, load_full = 0;
static long baud = 1000000;

static void put16(uint8_t *p, uint16_t v) { p[0] = v >> 8; p[1] = v; }
static void put32(uint8_t *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }

// Diagnostics straight from the FDCAN1 registers (the PMC transceiver is on PB8/PH13).
// LEC = last error code: ACK = nobody acknowledges (empty bus or different bit rate);
// STUFF/FORM/BIT0/BIT1/CRC = bad signal (wiring, H/L swapped or shorted, termination).
static char const *const LEC_NAME[] = {"none", "STUFF", "FORM", "ACK", "BIT1", "BIT0", "CRC", "-"};
static uint32_t busoff_recover = 0;

static void printBusState() {
  uint32_t const ecr = FDCAN1->ECR;
  uint32_t const psr = FDCAN1->PSR;
  bool const bo = psr & FDCAN_PSR_BO;
  Serial.print(" | TEC="); Serial.print(ecr & 0xFF);
  Serial.print(" REC="); Serial.print((ecr >> 8) & 0x7F);
  Serial.print(bo ? " BUS-OFF" : ((psr & FDCAN_PSR_EP) ? " PASSIVE" : ((psr & FDCAN_PSR_EW) ? " WARN" : " ACTIVE")));
  Serial.print(" LEC="); Serial.print(LEC_NAME[psr & 0x7]);
  Serial.print(" recov="); Serial.println(busoff_recover);
  if (bo && (FDCAN1->CCCR & FDCAN_CCCR_INIT)) {
    // leave bus-off: the controller rejoins after 128 x 11 recessive bits
    FDCAN1->CCCR &= ~FDCAN_CCCR_INIT;
    busoff_recover++;
  }
}

static void send(uint32_t id, uint8_t const *data, uint8_t len) {
  CanMsg msg(id, len, data);
  if (MachineControl_CANComm.write(msg) <= 0) {
    // without ACK (gateway unplugged or other bit rate) the TX queue fills up: count and go on
    tx_err++;
  }
}

static void setBaud(long b) {
  CanBitRate r;
  switch (b) {
    case 125000:  r = CanBitRate::BR_125k;  break;
    case 250000:  r = CanBitRate::BR_250k;  break;
    case 500000:  r = CanBitRate::BR_500k;  break;
    case 1000000: r = CanBitRate::BR_1000k; break;
    default: Serial.println("invalid baud: 125000 250000 500000 1000000"); return;
  }
  MachineControl_CANComm.end();
  if (!MachineControl_CANComm.begin(r)) {
    Serial.println("CAN init failed");
    return;
  }
  baud = b;
  Serial.print("baud "); Serial.println(baud);
}

static void handleSerial() {
  static char line[32];
  static uint8_t n = 0;
  while (Serial.available()) {
    char const c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (n < sizeof(line) - 1) line[n++] = c;
      continue;
    }
    line[n] = 0;
    n = 0;
    char const *arg = strchr(line, ' ');
    long const v = arg ? atol(arg + 1) : 0;
    if (!strncmp(line, "baud ", 5)) {
      setBaud(v);
    } else if (!strncmp(line, "load ", 5)) {
      load_fps = v; load_cnt = 0; load_full = 0; load_t0 = micros();
      Serial.print("load "); Serial.println(load_fps);
    } else {
      Serial.println("commands: baud <125000|250000|500000|1000000>, load <frames/s>");
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // no while(!Serial): it must run without a serial monitor too

  if (!MachineControl_CANComm.begin(CanBitRate::BR_1000k)) {
    Serial.println("CAN init failed");
    while (1) delay(1000);
  }
  Serial.println("PMC_CAN_Sim started, CAN 1 Mbit/s");
}

void loop() {
  uint32_t const now = millis();
  handleSerial();

  if (load_fps) {
    // how many frames should have gone out since the burst started; when the TX queue is
    // full retry on the next pass, so the counter never has holes on the sender side
    uint32_t const due = (uint64_t)(micros() - load_t0) * load_fps / 1000000ULL;
    while (load_cnt < due) {
      uint8_t d[8];
      put32(d, load_cnt);
      put32(d + 4, micros());
      CanMsg msg(ID_LOAD, 8, d);
      if (MachineControl_CANComm.write(msg) <= 0) {
        load_full++;
        break;
      }
      load_cnt++;
    }
  }

  if (now - t_fast >= 100) {
    t_fast = now;
    uint8_t d[8];
    int16_t const sine = (int16_t)(1000.0f * sinf(2.0f * PI * (now % 10000) / 10000.0f));
    put16(d, (uint16_t)cnt_fast);
    put16(d + 2, (uint16_t)sine);
    put32(d + 4, now);
    send(ID_FAST, d, 8);
    cnt_fast++;
  }

  if (now - t_slow >= 1000) {
    t_slow = now;
    float const ph = 2.0f * PI * (now % 60000) / 60000.0f;
    uint8_t d[8];
    put16(d, (uint16_t)(2640 + 40 * sinf(ph)));            // 26.00..26.80 V
    put16(d + 2, (uint16_t)(int16_t)(150 * cosf(ph)));      // -15..+15 A
    d[4] = (uint8_t)(50 + 30 * sinf(ph));                    // SOC 20..80 %
    d[5] = (uint8_t)(25 + 5 * cosf(ph));                     // 20..30 degC
    put16(d + 6, (uint16_t)cnt_slow);
    send(ID_BATT, d, 8);

    uint8_t e[8];
    put32(e, cnt_slow);
    put32(e + 4, 0xCAFEBABE);
    send(CanExtendedId(ID_EXT), e, 8);
    cnt_slow++;

    Serial.print("tx fast="); Serial.print(cnt_fast);
    Serial.print(" slow="); Serial.print(cnt_slow);
    Serial.print(" err="); Serial.print(tx_err);
    Serial.print(" rx="); Serial.print(rx_cnt);
    Serial.print(" baud="); Serial.print(baud);
    if (load_fps) {
      Serial.print(" load="); Serial.print(load_cnt);
      Serial.print(" txq_full="); Serial.print(load_full);
    }
    printBusState();
  }

  while (MachineControl_CANComm.available()) {
    CanMsg const msg = MachineControl_CANComm.read();
    rx_cnt++;
    Serial.print("RX ");
    Serial.println(msg);
    if (msg.isStandardId() && msg.getStandardId() == ID_PING) {
      send(ID_PONG, msg.data, msg.data_length);
    }
  }
}
