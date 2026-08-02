"""
rf24scan v1.0.0 - 2.4 GHz spectrum scanner firmware
Raspberry Pi Pico (RP2040) + nRF24L01+PA+LNA, MicroPython.

============================  RECEIVE ONLY  ============================
This firmware NEVER transmits. The radio is held in PRIM_RX for its whole
life; no W_TX_PAYLOAD, no CE-pulse-in-TX-mode, no carrier-wave test mode.
RF_PWR is pinned to its minimum setting purely as belt-and-braces - it
only affects a transmitter that is never enabled.
=======================================================================

HOW IT WORKS
  The nRF24L01+ has no true RSSI. It has a 1-bit Received Power Detector
  (RPD): set when the receiver sees > -64 dBm on the tuned channel. One
  read is a coin flip; the signal is in the *rate*. So we visit every
  channel many times and stream a hit COUNT per channel - that count over
  the number of passes is an occupancy probability, which is the actual
  measurement this tool is built on.

  Timing floor: after each channel change the PLL needs ~130-200 us to
  relock before RPD means anything. 126 channels x 200 us = 25 ms of pure
  waiting per pass, in any programming language. That is why this is
  MicroPython and not C - the radio, not the interpreter, sets the pace.

WIRING (SPI0)
  nRF24        Pico
  -----        ----------------------
  GND     ->   pin 38  (GND)
  VCC     ->   pin 36  (3V3 OUT)      <- 3.3 V ONLY. 5 V destroys the chip.
  CE      ->   GP2     (pin 4)
  CSN     ->   GP5     (pin 7)
  SCK     ->   GP6     (pin 9)
  MOSI    ->   GP7     (pin 10)
  MISO    ->   GP4     (pin 6)
  IRQ     ->   not connected          <- not needed, we poll RPD

  Solder a 10 uF capacitor across VCC/GND at the module. The PA+LNA front
  end draws current spikes that brown out a bare Pico 3V3 pin; this is the
  single most common reason these modules "don't work".

SERIAL PROTOCOL  (USB CDC, line oriented, 8N1, baud irrelevant)

  Pico -> host
    #rf24scan <version>                  banner, sent once on boot
    #info k=v k=v ...                    current settings
    #err <message>                       fault (e.g. radio not found)
    #hb <ms>                             heartbeat, once/sec while paused
    S <seq> <passes> <dwell> <ms> <lo> <hi> <hex>
        one frame. <hex> is two lowercase hex digits per channel from
        <lo> to <hi> inclusive; each value is 0..<passes> hits.

  host -> Pico  (one command per line, LF or CRLF)
    ?            print banner + #info
    G            go / resume streaming
    H            halt / pause
    D<us>        dwell per sample, 40..2000        (default 200)
    P<n>         passes accumulated per frame, 1..64 (default 8)
    C<lo>,<hi>   channel range, 0..125             (default 0,125)
    B<n>         RX bandwidth 0=250kbps 1=1Mbps 2=2Mbps (default 1)
    T            re-run the radio self-test
    Z            reset sequence counter

FLASHING: see ../SETUP.md
"""

import sys
import select
import time
from machine import Pin, SPI

VERSION = "1.0.0"

# ------------------------------------------------------------------ wiring
PIN_CE, PIN_CSN = 2, 5
PIN_SCK, PIN_MOSI, PIN_MISO = 6, 7, 4
SPI_ID = 0
SPI_BAUD = 8_000_000          # nRF24L01+ tolerates 10 MHz; 8 leaves margin

# ------------------------------------------------------- nRF24L01+ registers
REG_CONFIG = 0x00
REG_EN_AA = 0x01
REG_EN_RXADDR = 0x02
REG_SETUP_RETR = 0x04
REG_RF_CH = 0x05
REG_RF_SETUP = 0x06
REG_STATUS = 0x07
REG_RPD = 0x09

CMD_W_REGISTER = 0x20
CMD_FLUSH_RX = 0xE2

# CONFIG: IRQs masked, CRC disabled (we want raw energy, not valid packets),
# PWR_UP, PRIM_RX. Bit layout: MASK_RX_DR|MASK_TX_DS|MASK_MAX_RT|EN_CRC|CRCO|PWR_UP|PRIM_RX
CONFIG_RX = 0x73

# RF_SETUP data-rate encodings. Rate sets the receiver bandwidth, which is
# what actually decides frequency selectivity:
#   250 kbps -> narrowest, sharpest per-channel resolution, least sensitive to
#               wideband energy
#   1 Mbps   -> ~1 MHz, matches the 1 MHz channel grid  (default, best balance)
#   2 Mbps   -> ~2 MHz, catches more energy but smears adjacent channels
RATES = (0x20, 0x00, 0x08)
RATE_NAMES = ("250kbps", "1Mbps", "2Mbps")

CH_MIN, CH_MAX = 0, 125
NCH = CH_MAX - CH_MIN + 1

# ------------------------------------------------------------------ defaults
DEFAULT_DWELL = 200           # us of RX per sample, PLL relock floor is ~130
DEFAULT_PASSES = 8            # sweeps accumulated into one streamed frame
DWELL_MIN, DWELL_MAX = 40, 2000
PASSES_MIN, PASSES_MAX = 1, 64

# ------------------------------------------------------------------ hardware
CE = Pin(PIN_CE, Pin.OUT, value=0)
CSN = Pin(PIN_CSN, Pin.OUT, value=1)
SPI_BUS = SPI(
    SPI_ID,
    baudrate=SPI_BAUD,
    polarity=0,
    phase=0,
    bits=8,
    firstbit=SPI.MSB,
    sck=Pin(PIN_SCK),
    mosi=Pin(PIN_MOSI),
    miso=Pin(PIN_MISO),
)

try:
    LED = Pin(25, Pin.OUT)        # RP2040 Pico onboard LED
except Exception:
    LED = None

# Preallocated everywhere in the hot path: allocating inside the sweep would
# invite a GC pause mid-measurement and skew the dwell timing.
_w = bytearray(2)
_r = bytearray(2)
_counts = bytearray(NCH)
_hexbuf = bytearray(NCH * 2)
_HEXD = b"0123456789abcdef"


def write_reg(reg, val):
    _w[0] = CMD_W_REGISTER | reg
    _w[1] = val
    CSN(0)
    SPI_BUS.write(_w)
    CSN(1)


def read_reg(reg):
    _w[0] = reg
    _w[1] = 0xFF
    CSN(0)
    SPI_BUS.write_readinto(_w, _r)
    CSN(1)
    return _r[1]


def command(cmd):
    CSN(0)
    SPI_BUS.write(bytes((cmd,)))
    CSN(1)


def radio_present():
    """Write/read-back a scratch register to prove the SPI wiring is real.

    A floating MISO reads back all 0x00 or all 0xFF, so probing several
    distinct values catches a stuck line as well as a dead chip.
    """
    saved = read_reg(REG_RF_CH)
    ok = True
    for probe in (0x2A, 0x15, 0x01):
        write_reg(REG_RF_CH, probe)
        if read_reg(REG_RF_CH) != probe:
            ok = False
            break
    write_reg(REG_RF_CH, saved & 0x7F)
    return ok


def radio_init(rate_idx):
    CE(0)
    CSN(1)
    time.sleep_ms(5)
    write_reg(REG_CONFIG, 0x00)              # power down before reconfiguring
    time.sleep_ms(5)
    write_reg(REG_EN_AA, 0x00)               # no auto-acknowledge (would TX!)
    write_reg(REG_EN_RXADDR, 0x00)           # no RX pipes: carrier energy only
    write_reg(REG_SETUP_RETR, 0x00)          # no retransmit (would TX!)
    write_reg(REG_RF_SETUP, RATES[rate_idx])  # rate + RF_PWR=min
    write_reg(REG_STATUS, 0x70)              # clear latched IRQ flags
    command(CMD_FLUSH_RX)
    write_reg(REG_CONFIG, CONFIG_RX)         # PWR_UP + PRIM_RX
    time.sleep_ms(2)                         # Tpd2stby


@micropython.native
def sweep_pass(counts, lo, hi, dwell):
    """One visit to every channel in [lo, hi]. Increments counts in place."""
    csn = CSN
    ce = CE
    spi = SPI_BUS
    w = _w
    r = _r
    su = time.sleep_us
    for ch in range(lo, hi + 1):
        # Change channel while in Standby-I (CE low).
        w[0] = CMD_W_REGISTER | REG_RF_CH
        w[1] = ch
        csn(0)
        spi.write(w)
        csn(1)
        # Enter RX, let the PLL settle, drop back to standby. RPD latches on
        # the transition to standby, so it is safe to read after CE goes low.
        ce(1)
        su(dwell)
        ce(0)
        w[0] = REG_RPD
        w[1] = 0xFF
        csn(0)
        spi.write_readinto(w, r)
        csn(1)
        if r[1] & 1:
            counts[ch - lo] += 1


def emit_frame(seq, n, passes, dwell, lo, hi, ms):
    hb = _hexbuf
    c = _counts
    j = 0
    for i in range(n):
        v = c[i]
        hb[j] = _HEXD[(v >> 4) & 0xF]
        hb[j + 1] = _HEXD[v & 0xF]
        j += 2
    sys.stdout.write(
        "S %d %d %d %d %d %d %s\n"
        % (seq, passes, dwell, ms, lo, hi, bytes(hb[: n * 2]).decode())
    )


class Scanner:
    def __init__(self):
        self.dwell = DEFAULT_DWELL
        self.passes = DEFAULT_PASSES
        self.lo = CH_MIN
        self.hi = CH_MAX
        self.rate = 1
        self.running = True
        self.seq = 0
        self.radio_ok = False

    # ------------------------------------------------------------- reporting
    def banner(self):
        sys.stdout.write("#rf24scan %s\n" % VERSION)

    def info(self):
        sys.stdout.write(
            "#info radio=%s dwell=%d passes=%d lo=%d hi=%d rate=%s state=%s\n"
            % (
                "ok" if self.radio_ok else "MISSING",
                self.dwell,
                self.passes,
                self.lo,
                self.hi,
                RATE_NAMES[self.rate],
                "run" if self.running else "halt",
            )
        )

    # ------------------------------------------------------------- self test
    def self_test(self):
        self.radio_ok = radio_present()
        if self.radio_ok:
            radio_init(self.rate)
        else:
            sys.stdout.write(
                "#err nRF24 not responding on SPI0 - check CSN=GP5 SCK=GP6 "
                "MOSI=GP7 MISO=GP4, 3V3 on pin 36, and the 10uF cap\n"
            )
        return self.radio_ok

    # -------------------------------------------------------------- commands
    def handle(self, line):
        line = line.strip()
        if not line:
            return
        k = line[0].upper()
        arg = line[1:].strip()
        try:
            if k == "?":
                self.banner()
                self.info()
            elif k == "G":
                self.running = True
                self.info()
            elif k == "H":
                self.running = False
                self.info()
            elif k == "D":
                v = int(arg)
                if not (DWELL_MIN <= v <= DWELL_MAX):
                    raise ValueError("dwell out of range")
                self.dwell = v
                self.info()
            elif k == "P":
                v = int(arg)
                if not (PASSES_MIN <= v <= PASSES_MAX):
                    raise ValueError("passes out of range")
                self.passes = v
                self.info()
            elif k == "C":
                a, b = arg.split(",")
                a, b = int(a), int(b)
                if not (CH_MIN <= a <= b <= CH_MAX):
                    raise ValueError("bad channel range")
                self.lo, self.hi = a, b
                self.info()
            elif k == "B":
                v = int(arg)
                if not (0 <= v <= 2):
                    raise ValueError("bad rate")
                self.rate = v
                radio_init(self.rate)
                self.info()
            elif k == "T":
                self.self_test()
                self.info()
            elif k == "Z":
                self.seq = 0
                self.info()
            else:
                sys.stdout.write("#err unknown command %r\n" % k)
        except Exception as e:
            sys.stdout.write("#err %s: %s\n" % (k, e))


def main():
    sc = Scanner()
    sc.banner()
    sc.self_test()
    sc.info()

    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    cmdbuf = ""
    last_hb = time.ticks_ms()
    led_state = 0

    while True:
        # --- drain any host commands without blocking -----------------------
        guard = 0
        while poller.poll(0) and guard < 128:
            guard += 1
            ch = sys.stdin.read(1)
            if not ch:
                break
            if ch in "\r\n":
                if cmdbuf:
                    sc.handle(cmdbuf)
                    cmdbuf = ""
            elif len(cmdbuf) < 32:
                cmdbuf += ch

        if not sc.running or not sc.radio_ok:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_hb) >= 1000:
                last_hb = now
                sys.stdout.write("#hb %d\n" % now)
                if not sc.radio_ok:
                    # Keep retrying: the usual cause is a loose jumper or a
                    # brownout, both of which can come good without a reboot.
                    sc.self_test()
            time.sleep_ms(20)
            continue

        # --- one frame = `passes` accumulated sweeps -------------------------
        lo, hi = sc.lo, sc.hi
        n = hi - lo + 1
        for i in range(n):
            _counts[i] = 0

        t0 = time.ticks_ms()
        for _ in range(sc.passes):
            sweep_pass(_counts, lo, hi, sc.dwell)
        ms = time.ticks_diff(time.ticks_ms(), t0)

        sc.seq += 1
        emit_frame(sc.seq, n, sc.passes, sc.dwell, lo, hi, ms)

        if LED is not None:
            led_state ^= 1
            LED(led_state)


main()
