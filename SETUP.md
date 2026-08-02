# Setup — flash it, run it

Two things to do: put the firmware on the Pico, then open one HTML file.
Budget about 10 minutes.

---

## 0. Before you power anything

**Solder a 10 µF capacitor across VCC and GND at the module.**

The PA+LNA front end pulls current in spikes that brown out a bare Pico 3V3
pin. When these modules "don't work", this is nearly always why. Receive-only
scanning is gentler than transmitting, so you may get away without it — but if
your readings are erratic or the radio drops out mid-session, fit the cap
before you debug anything else.

**VCC is 3.3 V only.** 5 V destroys the chip. The *logic* pins are 5 V
tolerant; the supply pin is not.

### Wiring (already soldered on your unit)

| nRF24 | Pico | Pin | Why this pin |
|---|---|---|---|
| GND | GND | 38 | |
| VCC | 3V3 OUT | 36 | **3.3 V only** |
| CE | GP2 | 4 | plain GPIO — puts the radio in/out of RX |
| CSN | GP5 | 7 | SPI chip select, driven in software |
| SCK | GP6 | 9 | SPI0 SCK |
| MOSI | GP7 | 10 | SPI0 TX |
| MISO | GP4 | 6 | SPI0 RX |
| IRQ | — | — | not needed; the firmware polls RPD |

Screw the antenna on before powering up. Running a PA module with no antenna
stresses the front end.

---

## 1. Flash the firmware — one file, one drag

The `.uf2` already contains **MicroPython and the scanner together**. You do not
need to install MicroPython separately, and you do not need Thonny.

1. Open the web tool and click **Get firmware**, then pick your board:
   <https://qutaiba-khader.github.io/rf24-scanner/>
   (or grab it straight from the [latest release](https://github.com/Qutaiba-Khader/rf24-scanner/releases/latest))

   | board | file |
   |---|---|
   | Pico (RP2040) | `rf24scan-pico.uf2` |
   | Pico 2 (RP2350) | `rf24scan-pico2.uf2` |
   | Pico W / Pico 2 W | `rf24scan-pico_w.uf2` |

2. Unplug the Pico.
3. Hold **BOOTSEL**, plug the USB cable in, then release.
4. A drive called **RPI-RP2** appears.
5. Drag the `.uf2` onto it. The drive disconnects and the board reboots.

The onboard LED blinks for about 3 seconds while USB enumerates, then the
scanner starts. That blink is deliberate — see the troubleshooting note below.

**Not sure which board you have?** In BOOTSEL, open `INFO_UF2.TXT` on the
RPI-RP2 drive and read `Board-ID`: `RPI-RP2` is a Pico, `RP2350` is a Pico 2.
Flashing the wrong one does no damage — the board just will not start. Hold
BOOTSEL again and drag the right file over it.

---

## 2. Alternative: run it on stock MicroPython

Only if you already run MicroPython, or want to edit the code live.

Flash a stock build from <https://micropython.org/download/RPI_PICO/>, then copy
`main.py` onto the board:

**Thonny** — install from <https://thonny.org>, select *MicroPython (Raspberry Pi
Pico)* bottom-right, open `firmware/main.py`, then **File → Save as… →
Raspberry Pi Pico** and name it exactly `main.py`.

**mpremote** —

```powershell
pip install mpremote
mpremote connect auto cp firmware\main.py :main.py
mpremote connect auto reset
```

⚠️ **Close Thonny before connecting the browser.** A serial port can only be held
by one program at a time; if Thonny still has it open, Connect will fail.

---

## 3. Open the web tool

Double-click **`web/index.html`**. That is it — no server, no install, nothing
to build. It works offline and nothing ever leaves your machine.

1. Click **Connect Pico**.
2. Pick it in the browser's dialog. The list is filtered to Raspberry Pi
   devices, and from v1.0.2 the board identifies itself as
   **"rf24scan spectrum scanner"** rather than the generic *"Board in FS mode"*
   every MicroPython board otherwise reports. If your board uses a different USB
   chip and the list comes up empty, use **Show every device**.
3. The waterfall starts moving within a second.

**Chrome, Edge or Opera on desktop.** Firefox and Safari do not implement the
Web Serial API and never will — it is a deliberate decision by both vendors,
not a bug you can work around.

### If Connect is refused for a "secure context"

Rare, but if it happens:

```powershell
python C:\Users\qzaid\rf24_scanner\web\serve.py
```

That serves the same file at `http://localhost:8000`, which is always a
trusted origin. Same tool, same behaviour.

---

## 5. Try it with no hardware first

Click **Demo signal**. It synthesises a plausible room — a Wi-Fi access point
on channel 6, a weaker one on channel 11, BLE beacons, Bluetooth hopping, and
a narrowband emitter parked at 2441 MHz — so you can learn the UI and practise
Compare mode before you have the radio working.

In demo mode the **Capture A / Capture B** buttons switch the synthetic
emitter off and on for you, standing in for you unplugging the real box.

---

## Controls

| Control | What it does |
|---|---|
| **Passes per frame** | Samples per channel per frame. More = steadier probability, slower waterfall. 8 is a good default. |
| **Dwell** | RX time per sample. **Do not go below ~130 µs** — the PLL has not relocked and readings become noise. |
| **RX bandwidth** | 250 kbps is narrowest and sharpest; 2 Mbps catches more energy but smears neighbouring channels. 1 Mbps matches the 1 MHz channel grid. |
| **Pause** | Halts the sweep, keeps the connection. |
| **Re-test radio** | Re-runs the SPI self-test. Use after re-seating a wire. |

---

## Troubleshooting

**"nRF24 not responding on SPI0" in the log**

The firmware writes a scratch register and reads it back; that check failed.
In order of likelihood:

1. The 10 µF cap is missing and the module is browning out.
2. A wire is on the wrong pin — recheck MISO=GP4 and MOSI=GP7 specifically,
   they are the easiest pair to swap.
3. VCC is on 5 V (pin 40, `VBUS`) instead of 3V3 (pin 36). If it has been
   there a while the chip may already be dead.
4. Cold solder joint. Reflow and retry — the firmware retries once a second
   on its own, so a good joint shows up immediately in the log.

**No COM port at all, and Device Manager shows "Unknown USB Device (Device
Descriptor Request Failed)"**

Fixed in v1.0.1 — update if you are on v1.0.0. A `main.py` frozen into a `.uf2`
starts running early enough to collide with USB enumeration, and the scan loop
busy-waited without ever yielding, so the USB stack never got serviced. v1.0.1
settles for 3 seconds at boot (that is the LED blink) and yields once per pass.

To confirm the board itself is healthy: hold BOOTSEL and plug it in. If the
**RPI-RP2** drive appears, the cable, port and board are all fine and it is
purely a firmware problem — reflash.

**The port does not appear in the browser picker**

Thonny or another terminal still holds it. Close them. Failing that, unplug
and replug the Pico.

A crashed program can leave a serial port wedged — Windows then refuses it with
*Access is denied* even though nothing visibly holds it. Unplug and replug the
board; that resets the port and releases the handle.

**Everything reads 100% on every channel**

Front-end overload — something very strong is very close. Move the scanner
away and re-check. The verdict panel flags this on its own by watching the
above-ISM control zone.

**Everything reads 0% on every channel**

The RPD threshold is a fixed −64 dBm, which is quite deaf. Either the room is
genuinely quiet, or the antenna is not screwed on. Confirm the tool is alive
by holding a 2.4 GHz source (a phone with a hotspot on) next to the antenna.

**The waterfall starts mostly black**

Normal. It holds 300 rows of history and fills top-down at roughly 3 frames a
second, so it takes a minute or two to fill the pane.
