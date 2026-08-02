# rf24scan

A 2.4 GHz spectrum scanner built from a Raspberry Pi Pico and an
nRF24L01+PA+LNA, with a browser front end. Sweeps all 126 channels
(2400–2525 MHz), streams occupancy over USB, and draws a live waterfall,
bar graph and verdict panel.

Built to answer one question: **which device in this room is interfering with
my earbuds and my wireless mouse dongle?**

Both victims are 2.4 GHz, and both dropping out points at a *wideband*
aggressor — see the hunting guide in [`INTERPRETING.md`](INTERPRETING.md).

**Receive only.** The firmware never transmits — no jamming, no injection.

```
nRF24L01+PA+LNA ──SPI0──► Pico ──USB serial──► Chrome ──Web Serial──► index.html
   (RX only)              126 ch sweep       text frames      waterfall + verdict
```

## Files

| Path | What it is |
|---|---|
| `firmware/main.py` | MicroPython firmware. Copy to the Pico as `main.py`. |
| `web/index.html` | The whole tool. One file, no dependencies, no network. |
| `web/serve.py` | Fallback local server. Only needed if `file://` is refused. |
| `tools/scan.py` | Command-line scanner — no browser needed. `--label --save --html --compare` |
| `tools/timeline.py` | Records what was powered on for each capture; `diff` finds contiguous clusters |
| `tools/report.py` + `build_report.py` | Builds the HTML report with device attribution and the interception map |
| `tools/results_md.py` | Writes `RESULTS.md` — every finding in plain tables |
| `SETUP.md` | Wiring, flashing, running, troubleshooting. |
| `INTERPRETING.md` | What the numbers mean and how to fix what you find. |
| `FINDINGS.md` | **The results, every bug found, and what still needs fixing.** |

## Quick start

1. Solder a **10 µF cap across VCC/GND** at the module (see `SETUP.md`).
2. Flash the `.uf2` for your board (hold BOOTSEL, drag). It already contains MicroPython.
3. **Close Thonny** — one program per serial port.
4. Double-click `web/index.html`, click **Connect Pico**.

No hardware yet? Open the tool with **`?demo=1`** on the URL
(<https://qutaiba-khader.github.io/rf24-scanner/?demo=1>) and click **Demo
signal** — it synthesises a room with a Wi-Fi AP, BLE beacons, Bluetooth hopping
and a narrowband emitter at 2441 MHz, so you can practise Compare mode before
the radio is wired. The button is hidden without that flag to keep the normal
UI uncluttered.

## What it does

- **Waterfall** — 126 channels × 300 rows of history, single-hue blue ramp
  (inferno available for SDR muscle memory).
- **Live bar graph** — current sweep, rolling average, peak hold.
- **Protocol lanes** — Wi-Fi 1/6/11/13, BT Classic, BLE advertising, Zigbee,
  and the above-ISM control zone.
- **Compare mode** — capture baseline (device off) vs test (device on), then
  see exactly which channels the device owns. This is how you fingerprint a box.
- **Verdict panel** — names Wi-Fi plateaus, narrowband emitters, hopping
  traffic and receiver overload, then tells you what to do about it.
- **Export** — session JSON, summary CSV, waterfall CSV, comparison CSV,
  waterfall PNG. Sessions reload for later analysis.

## Design notes

**Why MicroPython, not C.** The nRF24 needs ~130–200 µs after each channel
change for its PLL to relock before the RPD bit means anything. That is 25 ms
of pure waiting per 126-channel sweep in *any* language. MicroPython lands at
~40 ms/sweep against C's ~28 ms — a 1.4× difference that does not justify a
CMake and arm-none-eabi toolchain. Edit `main.py` and reset; no build step.

**Why Web Serial, not a local server.** Zero install, works offline, nothing
leaves the machine. Kept to a single HTML file deliberately: ES-module
`import` is blocked by CORS on `file://`, so splitting it up would *force* a
server. Cost: Chrome/Edge/Opera desktop only — Firefox and Safari do not
implement Web Serial.

**Why a single-hue waterfall.** Rainbow ramps (turbo, jet) are the SDR
convention but read magnitude poorly — the eye cannot order hues, only
lightness. The default is one hue light→dark. Inferno is there as an opt-in.

**Why channels 84–125 matter.** They are above the 2483.5 MHz ISM edge, so
nothing legitimate lives there. That makes them a free noise-floor reference:
if they light up, the front end is being overloaded and every other reading is
inflated.

## Status — hardware verified, and it answered the question

Firmware **v1.1.1** runs on a real RP2040 Pico, radio reports `radio=ok`,
~408 ms per sweep. Twelve captures taken 2026-08-02.

| Device | Band | nRF ch | Adds | Confidence |
|---|---|---|---|---|
| **FancyLEDs box (Tuya)** | 2414–2430 MHz | ch 14–30 | +22 | **99%** |
| **Xbox** | 2414–2430 MHz | ch 14–30 | +30 in use / +6 idle | **98%** |
| Galaxy Buds + transmitter | 2458–2466 MHz | ch 58–66 | +5 | 53% |
| Unidentified | 2417–2428 MHz | ch 17–28 | 21% | untested |

Together the first two take **17 of the 79 channels** Bluetooth hops through.

Full write-up, every bug found, and what still needs doing:
**[FINDINGS.md](FINDINGS.md)**.

**Still untested against real hardware: the browser tool.** Every capture above
was taken with `tools/scan.py`; the web UI has only ever seen synthetic data.

## Two rules that decide whether this works

1. **Take the scanner to within 10–20 cm of the suspect.** The RPD floor is a
   hard −64 dBm. A Tuya LED box measured three times from across the room showed
   *nothing*; at 10 cm it took 2415 MHz from 1.3% to 56%.
2. **60-second captures, and repeat every trial.** Two 25 s runs produced
   clusters in different places, and both turned out to be noise.
