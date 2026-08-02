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
| `SETUP.md` | Wiring, flashing, running, troubleshooting. |
| `INTERPRETING.md` | What the numbers mean and how to fix what you find. |

## Quick start

1. Solder a **10 µF cap across VCC/GND** at the module (see `SETUP.md`).
2. Flash MicroPython (`RPI_PICO` build) and copy `firmware/main.py` as `main.py`.
3. **Close Thonny** — one program per serial port.
4. Double-click `web/index.html`, click **Connect Pico**.

No hardware yet? Open `web/index.html` and click **Demo signal**.

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

## Status

Firmware and web tool are complete. The web tool has been exercised
end-to-end against synthetic data: connect flow, waterfall, bar chart, band
lanes, tooltips, table view, palette switching, Compare mode, the verdict
engine, and all five exports.

**Not yet tested against real hardware** — the Pico and radio were not
attached when this was built.
