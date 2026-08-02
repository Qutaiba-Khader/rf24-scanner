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

## 1. Flash MicroPython

Your board is an **RP2040 Pico**, so you want the `RPI_PICO` build.
Flashing the Pico 2 (RP2350) build onto an RP2040 fails silently — the board
just never comes back.

1. Download the latest `.uf2` from **<https://micropython.org/download/RPI_PICO/>**
2. Unplug the Pico.
3. Hold the **BOOTSEL** button, plug the USB cable in, then release BOOTSEL.
4. A drive called **RPI-RP2** appears.
5. Drag the `.uf2` onto it. The drive disconnects and the Pico reboots.

That is the only time you need BOOTSEL. From here on it is a normal USB device.

---

## 2. Copy the firmware

Pick either route.

### Thonny (easiest, has a GUI)

1. Install **Thonny** — <https://thonny.org>
2. Bottom-right corner → select **MicroPython (Raspberry Pi Pico)**.
3. **File → Open** → `firmware/main.py`
4. **File → Save as… → Raspberry Pi Pico** → name it exactly `main.py`

Named `main.py`, it runs automatically every time the Pico gets power.

### mpremote (command line)

```powershell
pip install mpremote
mpremote connect auto cp C:\Users\qzaid\rf24_scanner\firmware\main.py :main.py
mpremote connect auto reset
```

---

## 3. ⚠️ Close Thonny before connecting the browser

A serial port can only be held by one program at a time. If Thonny (or any
terminal) still has the Pico open, the browser's Connect will fail or the port
will not appear in the picker.

In Thonny: **Run → Stop/Restart**, then close Thonny entirely.

---

## 4. Open the web tool

Double-click **`web/index.html`**. That is it — no server, no install, nothing
to build. It works offline and nothing ever leaves your machine.

1. Click **Connect Pico**.
2. Pick the Pico in the browser's port dialog — on Windows it shows as
   *USB Serial Device (COMx)*.
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

**The port does not appear in the browser picker**

Thonny or another terminal still holds it. Close them. Failing that, unplug
and replug the Pico.

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
