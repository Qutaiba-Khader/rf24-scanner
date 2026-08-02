"""
rf24scan - 2.4 GHz spectrum scanner firmware
Raspberry Pi Pico / Pico 2 / Pico W + nRF24L01+PA+LNA, MicroPython.
Version is the VERSION constant below - deliberately not repeated here, so the
two cannot drift apart.

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
    S <seq> <passes> <dwell> <ms> <lo> <hi> <hex> <rate>
        one frame. <hex> is two lowercase hex digits per channel from
        <lo> to <hi> inclusive; each value is 0..<passes> hits.
        <rate> is 0/1/2 - the bandwidth this frame was measured at. Added
        in v1.2.0 as a TRAILING field, so pre-1.2.0 hosts (which read
        fields 0..6 positionally) keep working unchanged.

  host -> Pico  (one command per line, LF or CRLF)
    ?            print banner + #info
    G            go / resume streaming
    H            halt / pause
    D<us>        dwell per sample, 40..2000        (default 200)
    P<n>         passes accumulated per frame, 1..64 (default 8)
    C<lo>,<hi>   channel range, 0..125             (default 0,125)
    B<n>         RX bandwidth 0=250kbps 1=1Mbps 2=2Mbps (default 1)
                 3=cycle - rotate all three, one per frame, so the same
                 scene is measured at every bandwidth near-simultaneously
    T            re-run the radio self-test
    Z            reset sequence counter

WHAT THE ONBOARD LED MEANS  (the only diagnostic that works with no host)

  fast flutter, ~4/sec, for 3 seconds     starting up, letting USB enumerate
  then it settles into one of:
    steady blink ~1.5/sec                 SCANNING - radio found, all good
    slow blink, 1 toggle per second       alive, but the nRF24 is NOT answering
                                          -> check the 10uF cap, then MISO/MOSI
    DARK, no blinking at all              the firmware is not running

  The slow blink matters: an earlier version simply stopped blinking when the
  radio was missing, which is indistinguishable from a crash, and that is
  exactly how it got read.

FLASHING: see ../SETUP.md
"""

import sys
import select
import time
from machine import Pin, SPI

VERSION = "1.2.0"

# How long to leave the CPU alone at boot before starting to scan.
#
# When this file is FROZEN into a .uf2 it starts running far earlier than a
# main.py sitting on the filesystem does - early enough to overlap the host's
# USB enumeration. The sweep loop busy-waits in sleep_us() about a thousand
# times per frame and never yields, so TinyUSB goes unserviced and the descriptor
# request times out. Windows reports that as:
#     Unknown USB Device (Device Descriptor Request Failed)
# and no COM port ever appears. Standing still for a moment first lets
# enumeration finish; the yields in the main loop keep it alive afterwards.
BOOT_SETTLE_MS = 3000

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

# ------------------------------------------------------------------- output
#
# DO NOT reintroduce select.poll() on sys.stdout here.
#
# v1.0.3 tried to make writes non-blocking by registering sys.stdout with
# select.poll() for POLLOUT and checking poll(0) before every write. Polling
# sys.STDIN is the supported idiom in MicroPython; polling sys.STDOUT for
# writability is not. register() succeeded without complaining and then poll(0)
# HUNG - and a hang is not an exception, so the try/except around it caught
# nothing. On hardware the board flashed its 3-second start-up pattern (which
# makes no writes), then stopped dead on the very first say() call, taking USB
# enumeration down with it. v1.0.1, with plain writes, enumerated fine.
#
# MicroPython's CDC write does still block when the host stops draining the
# buffer, so a wedged host can stall the scan loop. That is a real trade, made
# knowingly: the browser side no longer stalls (its log used to be O(n^2) with a
# forced layout per line), and a blocking write that works beats a clever one
# that hangs the board before it can even enumerate.
# Stay SILENT until the host says something first.
#
# This is the whole fix for "LED flutters for 3 seconds, then goes dark".
# MicroPython's USB CDC write blocks while nothing is draining the port, and at
# boot nothing is: Windows enumerates the device but does not OPEN it. So the
# first write - the start-up banner - blocked forever and the scan loop never
# began. USB stayed enumerated, which is why the board looked healthy from the
# host while doing nothing at all.
#
# Polling sys.stdout for POLLOUT to detect this is NOT supported in MicroPython
# and hangs (see v1.0.3). There is no reliable "is the host reading?" call. But
# there is a reliable proxy: a host that has sent us a byte is a host that has
# the port open. So we say nothing until we hear something, and the web tool
# sends "?" the moment it connects.
_host_seen = False


def host_has_spoken():
    return _host_seen


def say(text):
    """Write a line, but only once a host has proved it is there."""
    if not _host_seen:
        return False
    try:
        sys.stdout.write(text)
        return True
    except Exception:
        return False


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


# Yield to the scheduler every N channels while sweeping.
#
# time.sleep_us() is a BUSY-WAIT and does not yield, so a pass would otherwise
# hold the CPU for ~25ms solid. Yielding every few channels lets TinyUSB run.
YIELD_EVERY = 8


# *** DO NOT PUT @micropython.native BACK ON THIS FUNCTION ***
#
# It was here from v1.0.0 and it is what broke USB for nine releases.
#
# From the MicroPython docs (reference/speed_python.html): "The background
# scheduler is NOT RUN during execution of native code... long running native
# functions should call time.sleep(0) periodically, which will run the
# scheduler."
#
# The whole 126-channel sweep ran inside a native function, so the scheduler -
# and therefore TinyUSB - was suspended for the entire pass, every pass. The
# board enumerated, then died the moment scanning began. Worse, the yields
# added above did nothing while the decorator was present, because the
# scheduler does not run inside native code at all.
#
# The decorator was never worth much here anyway: the 200us PLL dwell dominates
# this loop, so the interpreter is not the bottleneck. See the module docstring.
def sweep_pass(counts, lo, hi, dwell):
    """One visit to every channel in [lo, hi]. Increments counts in place."""
    csn = CSN
    ce = CE
    spi = SPI_BUS
    w = _w
    r = _r
    su = time.sleep_us
    sm = time.sleep_ms
    since = 0
    for ch in range(lo, hi + 1):
        since += 1
        if since >= YIELD_EVERY:
            since = 0
            sm(1)              # the only thing here that actually yields
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


def emit_frame(seq, n, passes, dwell, lo, hi, ms, rate):
    """Emit one sweep.

    The rate index is appended as a NEW TRAILING field. Every existing parser
    reads fields 0..7 positionally and only checks `len(parts) < 8`, so older
    hosts keep working untouched - which matters, because a firmware/host
    version mismatch has already cost this project nine pointless reflashes.
    """
    hb = _hexbuf
    c = _counts
    j = 0
    for i in range(n):
        v = c[i]
        hb[j] = _HEXD[(v >> 4) & 0xF]
        hb[j + 1] = _HEXD[v & 0xF]
        j += 2
    return say(
        "S %d %d %d %d %d %d %s %d\n"
        % (seq, passes, dwell, ms, lo, hi, bytes(hb[: n * 2]).decode(), rate)
    )


class Scanner:
    def __init__(self):
        self.dwell = DEFAULT_DWELL
        self.passes = DEFAULT_PASSES
        self.lo = CH_MIN
        self.hi = CH_MAX
        self.rate = 1
        # Cycle mode (B3): rotate 250k -> 1M -> 2M on successive frames.
        #
        # The rate sets the receiver's IF bandwidth, so comparing the same
        # scene at all three separates a WIDEBAND source (collects more power
        # in a wider receiver) from a NARROWBAND one (identical everywhere),
        # and a signal well above the -64 dBm threshold (flat) from one sitting
        # right on it (climbs). Doing that as three separate captures assumes
        # the room holds still for minutes - and in this room the Xbox alone
        # swings a band by +6 to +30 depending on whether it is in use.
        # Interleaving per frame makes the comparison genuinely simultaneous.
        self.cycle = False
        self.running = True
        self.seq = 0
        self.radio_ok = False

    # ------------------------------------------------------------- reporting
    def banner(self):
        say("#rf24scan %s\n" % VERSION)

    def info(self):
        say(
            "#info radio=%s dwell=%d passes=%d lo=%d hi=%d rate=%s state=%s\n"
            % (
                "ok" if self.radio_ok else "MISSING",
                self.dwell,
                self.passes,
                self.lo,
                self.hi,
                "cycle" if self.cycle else RATE_NAMES[self.rate],
                "run" if self.running else "halt",
            )
        )

    # ------------------------------------------------------------- self test
    def self_test(self):
        self.radio_ok = radio_present()
        if self.radio_ok:
            radio_init(self.rate)
        else:
            say(
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
                if not (0 <= v <= 3):
                    raise ValueError("bad rate")
                if v == 3:
                    self.cycle = True
                    self.rate = 0
                else:
                    self.cycle = False
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
                say("#err unknown command %r\n" % k)
        except Exception as e:
            say("#err %s: %s\n" % (k, e))


def main():
    # Let USB enumerate before this loop takes the CPU. See BOOT_SETTLE_MS.
    # sleep_ms yields to the scheduler (and therefore to TinyUSB); sleep_us
    # below ~1ms is a busy-wait and does not.
    if LED is not None:
        for _ in range(BOOT_SETTLE_MS // 250):
            LED(1); time.sleep_ms(125)
            LED(0); time.sleep_ms(125)
    else:
        time.sleep_ms(BOOT_SETTLE_MS)

    global _host_seen

    sc = Scanner()
    # No banner here: nothing is reading yet, and writing would block. The
    # greeting goes out the moment the host sends its first byte.
    sc.self_test()

    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    cmdbuf = ""
    last_hb = time.ticks_ms()
    led_state = 0
    dropped = 0

    while True:
        # --- drain any host commands without blocking -----------------------
        # poll() returns a list of (object, event) pairs. Testing it for mere
        # TRUTHINESS is a trap: with no host holding the port open, the USB CDC
        # reports POLLHUP/POLLERR, which is a NON-EMPTY list. The old code took
        # that as "data is waiting" and called sys.stdin.read(1), which blocks
        # forever on a stream that will never produce a byte.
        #
        # That is the real "LED flutters for 3 seconds then goes dark": the
        # flutter finishes, the main loop runs its first poll, and the board
        # wedges on the very first read. It has been there since v1.0.0 and
        # survived several fixes aimed at the wrong layer.
        #
        # So: only read when POLLIN is actually set.
        guard = 0
        while guard < 128:
            events = poller.poll(0)
            if not events:
                break
            if not any(ev & select.POLLIN for _obj, ev in events):
                break                      # hangup/error only - nothing to read
            guard += 1
            ch = sys.stdin.read(1)
            if not ch:
                break
            if not _host_seen:
                # First byte ever from the host: it has the port open, so it is
                # now safe to write without risking a permanent block. Greet it.
                _host_seen = True
                sc.banner()
                sc.info()
            if ch in "\r\n":
                if cmdbuf:
                    sc.handle(cmdbuf)
                    cmdbuf = ""
            elif len(cmdbuf) < 32:
                cmdbuf += ch

        # Do not sweep until a host is actually there.
        #
        # This is the other half of the same problem. The sweep loop is the
        # thing that starves USB, so running it unattended from boot is exactly
        # the pattern micropython#6853/#8904 describe. Idling on sleep_ms costs
        # nothing, keeps the USB stack healthy, and means the board always
        # enumerates - the scan starts when someone connects and says hello.
        if not _host_seen:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_hb) >= 500:
                last_hb = now
                if LED is not None:          # slow pulse: waiting for a host
                    led_state ^= 1
                    LED(led_state)
            time.sleep_ms(50)
            continue

        if not sc.running or not sc.radio_ok:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_hb) >= 1000:
                last_hb = now
                say("#hb %d\n" % now)
                if not sc.radio_ok:
                    # Keep retrying: the usual cause is a loose jumper or a
                    # brownout, both of which can come good without a reboot.
                    sc.self_test()
                # SLOW blink (1 toggle/sec) so "alive but no radio" is visibly
                # different from "dead". Previously the LED simply stopped here,
                # which is indistinguishable from a crash - and that is exactly
                # how it got read.
                if LED is not None:
                    led_state ^= 1
                    LED(led_state)
            time.sleep_ms(20)
            continue

        # --- one frame = `passes` accumulated sweeps -------------------------
        # In cycle mode the rate advances BEFORE the frame, so the rate index
        # emitted with the frame is the one the frame was actually measured at.
        # radio_init costs ~12 ms of sleep_ms against a ~400 ms frame (~3%), and
        # sleep_ms yields, so it stays USB-friendly.
        if sc.cycle:
            sc.rate = (sc.rate + 1) % 3
            radio_init(sc.rate)

        lo, hi = sc.lo, sc.hi
        n = hi - lo + 1
        for i in range(n):
            _counts[i] = 0

        t0 = time.ticks_ms()
        for _ in range(sc.passes):
            sweep_pass(_counts, lo, hi, sc.dwell)
            # One yield per pass (~1 ms on a ~40 ms pass, so ~2% of the time)
            # keeps the USB stack serviced. Without it a long frame can starve
            # TinyUSB badly enough to drop the connection mid-session.
            time.sleep_ms(1)
        ms = time.ticks_diff(time.ticks_ms(), t0)

        sc.seq += 1
        if emit_frame(sc.seq, n, sc.passes, sc.dwell, lo, hi, ms, sc.rate):
            if dropped:
                # Tell the host it missed data, now that it is listening again.
                say("#info dropped=%d host_was_not_reading\n" % dropped)
                dropped = 0
        else:
            dropped += 1
        time.sleep_ms(1)

        # Toggle regardless of whether the frame went out, so the LED means
        # "the scan loop is alive" and nothing else. If it stops, the firmware
        # stopped - not the USB link.
        if LED is not None:
            led_state ^= 1
            LED(led_state)


try:
    main()
except KeyboardInterrupt:
    pass                      # Ctrl-C from a terminal: drop to the REPL on purpose
except Exception as _e:       # noqa: BLE001 - deliberately catching everything
    # Say something before falling through to the REPL. A frozen build that
    # dies silently looks exactly like dead hardware from the host side.
    try:
        say("#err fatal: %s\n" % _e)
    except Exception:
        pass
    raise
