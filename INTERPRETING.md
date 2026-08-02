# Reading the results

## What you are actually measuring

The nRF24L01+ has **no RSSI**. It has one bit — the Received Power Detector —
which goes high when the tuned channel carries more than about **−64 dBm**.
It cannot tell you *how strong*. It only says *loud enough, or not*.

So a single reading is worthless. The tool visits every channel many times and
reports **how often** the bit was set:

> **Occupancy % = how often a carrier above −64 dBm was present on that channel.**

That number is a duty cycle, not a power level. 80% on a channel means
something was transmitting there 80% of the time you looked — it does **not**
mean it was four times stronger than a channel reading 20%.

Two consequences worth keeping in mind:

- **Anything below −64 dBm is invisible.** A weak or distant interferer reads
  as zero. Absence of evidence is not evidence of absence — move the scanner
  closer to the suspect and look again.
- **Percentages are comparable to each other, not to dBm.** This is a
  relative instrument. That is exactly what you need for "what changed when I
  turned the box on", and not what you need for "how many dBm is my signal".

---

## The band map

nRF24 channel *N* sits at **2400 + N MHz**. The overlay lanes under the
waterfall mark who owns what.

| Protocol | nRF channels | Notes |
|---|---|---|
| Wi-Fi ch 1 | 2–22 | 20 MHz wide, centre 2412 MHz |
| Wi-Fi ch 6 | 27–47 | centre 2437 MHz |
| Wi-Fi ch 11 | 52–72 | centre 2462 MHz |
| Wi-Fi ch 13 | 62–82 | EU/JP only — drawn dashed |
| Bluetooth Classic | 2–80 | 79 channels, hops ~1600×/sec |
| BLE advertising | 2, 26, 80 | channels 37 / 38 / 39 |
| Zigbee / Thread | 5, 10, 15 … 80 | every 5th channel |
| **Above ISM** | **84–125** | **should be silent** |

### The control zone is your free sanity check

The 2.4 GHz ISM band ends at 2483.5 MHz. Channels 84–125 are above it, so
nothing legitimate transmits there. Treat them as a built-in noise floor:

- **Reads near zero** → your measurements are trustworthy.
- **Reads more than ~15%** → your receiver is being overloaded. Something very
  strong is very close, and it is bleeding across the whole sweep. Every other
  number on screen is inflated until you move the scanner away.

The verdict panel watches this automatically and will tell you.

---

## The three signatures

Almost everything you will see is one of these three shapes.

### 1. A flat plateau, ~20 channels wide, steady → **Wi-Fi**

```
        ▁▁▆████████████████████▆▁▁
        2427        2437        2447
```

A block that wide with a flat top is an access point. Centre it on the table
above to name the channel. It does not move, and it will still be there
tomorrow.

### 2. A spike 1–3 channels wide, rock steady → **proprietary 2.4 GHz**

```
                    █
        ▁▁▁▁▁▁▁▁▁▁▁███▁▁▁▁▁▁▁▁▁▁▁
                  2441
```

**This is the interesting one, and the most likely culprit for your LED box.**
Cheap 2.4 GHz gear parks on one fixed frequency and never leaves: RGB/LED
controllers, wireless mouse and keyboard dongles, RF remotes, baby monitors,
wireless doorbells, cheap cameras.

Narrow and *unmoving* is the tell. Bluetooth hops; these do not.

### 3. Speckle smeared wide, different every frame → **Bluetooth / BLE**

```
        ▁▃▁▄▁▁▃▁▂▁▄▁▁▂▃▁▁▄▁▂▁▁▃▁▄▁
        2402                   2480
```

Frequency hopping — 1600 hops a second across 79 channels. You will never
catch it on one channel; you see it as a haze across the whole band that
changes every frame. Some of this haze is your own headphones.

**Special case — a broad, drifting lump around 2450–2470 MHz that comes and
goes in minutes: that is a microwave oven.** It is not a signature you can fix,
only avoid while it is running.

---

## Fingerprinting a device (Compare mode)

This is the reliable way to pin a specific box, and it is far more trustworthy
than eyeballing the live view.

1. Put the scanner **within a metre** of the suspect device.
2. Unplug the device. Wait for the waterfall to settle — 10 seconds or so.
3. Click **Capture A — OFF**. Wait for it to finish.
4. Plug the device in. Give it a moment to boot and start transmitting.
5. Click **Capture B — ON**.

The delta chart shows **B − A**. Red bars are channels that got louder with
the device on; that is the device's fingerprint. Grey means the change was
inside the noise and is not being claimed as real.

**Why the significance test matters.** A channel is only marked significant if
the change beats both a 6-point floor *and* two standard deviations of the two
captures combined. Without that, ordinary frame-to-frame jitter on a busy
channel would light up as a "finding" every time.

Reading the result:

| Fingerprint shape | Conclusion |
|---|---|
| One tight 1–4 channel block | Fixed-frequency proprietary transmitter. **This is your interferer.** |
| A ~20 channel block | The device joined Wi-Fi, or raised its own AP |
| Scattered, no dominant carrier | Bluetooth/BLE, or something that only talks periodically |
| Nothing significant | Not radiating in 2.4 GHz, too far away, or only transmits in bursts — try a longer capture and move closer |

Run one capture pair per suspect device and you can build a map of your whole
room. Export each as CSV to keep them.

---

## Your case: earbuds AND a mouse dongle both dropping out

Two different victims failing is more informative than one, so use it.

### What the two victims tell you

| Victim | What it is on air | What kills it |
|---|---|---|
| Earbuds | Bluetooth/BLE, hops across 2402–2480 with AFH | **Wideband** noise — it leaves nowhere to hop |
| Mouse dongle | Proprietary 2.4 GHz, narrow, fixed or small hop set | **Anything loud on its channels**, narrow or wide |

Bluetooth actively dodges busy channels. So if your **earbuds** are struggling,
the aggressor is probably **wide** — because a narrow one is exactly what AFH is
built to route around. A single LED remote parked on one channel is unlikely to
be the whole story.

That both victims fail points the same way. Start with the wideband suspects.

### The prime suspect, given these two symptoms

**USB 3.0 radiated noise.** USB 3 ports, cables and external SSDs emit broadband
noise straight across 2.4 GHz — Intel published a whitepaper on this exact
effect. It fits your symptoms unusually well:

- It is **wideband**, so it defeats Bluetooth AFH → earbuds suffer.
- Your mouse dongle is plugged **into the PC**, centimetres from the noise
  source → dongle suffers.
- It only appears when the port is active, which is why the problem
  "starts randomly" and seems to come and go.

Test it in two minutes, before building any fingerprint library:

1. Put the scanner next to the PC's USB ports.
2. **Capture A** with the external drive/hub unplugged and idle.
3. **Capture B** with it plugged in and copying a large file.

A broad lift across many channels confirms it. The fix is free: move the dongle
onto a short USB **2.0** extension cable, away from the case and away from any
USB 3 port.

### Building a fingerprint library

If USB 3 is not it, work through the room one device at a time:

1. Baseline the room with everything you can switch off, off.
2. For each suspect — the FancyLEDs box, the router, a smart plug, a camera, a
   TV, a microwave — run one **Capture A (off) → Capture B (on)** pair.
3. **Export the comparison CSV each time** and name it after the device.

After a handful you will have a channel map of your whole room, and the culprit
is whichever device owns the channels your victims need.

### ⚠️ Remember your own gear is also transmitting

The earbuds and the mouse dongle **show up in your own scan**. Before blaming
anything, run a capture pair on each of *them* so you know what they look like —
otherwise you will eventually "discover" an interferer that turns out to be the
device you are trying to protect.

Turning the victim off is also a useful test in reverse: if the band barely
changes when your earbuds are off, they are not the ones filling it.

---

## Fixing 2.4 GHz audio dropouts

Ordered by how much they typically buy you.

**1. Move the transmitter, not the headphones.**
Put the dongle or transmitter on a short USB extension away from the PC case.
This is usually the single biggest win, because of:

**2. USB 3.0 is a broadband 2.4 GHz noise source.**
USB 3.0 ports, cables and enclosures radiate right across the band. It is a
documented effect (Intel published a whitepaper on it) and it is very commonly
the real cause. If your dongle is next to a USB 3 port or an external SSD, move
it — or use a USB 2.0 port, which does not do this.

**3. Set your router's Wi-Fi channel by hand.**
The verdict panel names the quietest of 1/6/11 from your own measurements. Set
it explicitly. **Do not leave it on "auto"** — auto re-picks on its own
schedule and will silently undo your work.

**4. Move your Wi-Fi to 5 GHz where you can.**
It vacates the band rather than competing for it. The single most effective
change if your devices support it.

**5. Deal with the narrowband emitter you found.**
Once Compare mode names it, options are: relocate it, put it on a switched
socket so it is off when you are listening, or replace it with a Zigbee or
Wi-Fi equivalent that behaves better. Many cheap LED controllers transmit
continuously even when idle.

**6. Keep line of sight.**
Your own body between transmitter and headphones costs more signal than most
interference does. If dropouts correlate with you turning around, that is what
is happening, and no amount of channel tuning will fix it.

### Why a narrow interferer hurts less than you would expect

Bluetooth uses **Adaptive Frequency Hopping** — it detects busy channels and
removes them from its hop set. So one narrow spike costs you very little; BT
just routes around it.

What actually causes dropouts is **wideband** interference, because it leaves
Bluetooth nowhere to hop. That is why a microwave oven or a USB 3 enclosure
wrecks audio while a single-channel LED remote usually does not.

**If your fingerprint came back narrow and your audio still drops out, the
narrowband device is probably not your problem** — keep looking for something
wide.

---

## Limits of this instrument

Be honest with yourself about what it cannot do:

- **No power measurement.** Occupancy is a duty cycle. You cannot rank two
  interferers by strength from this data.
- **A fixed −64 dBm floor.** Weak signals are simply invisible.
- **Uncalibrated.** Readings depend on antenna orientation and distance. Do not
  compare numbers taken from two different scanner positions — when running
  Compare mode, **do not move the scanner between A and B**, or the difference
  you measure is your own hand.
- **~3 sweeps per second.** Short bursts between sweeps are missed entirely.
  A device that transmits for 5 ms once a second may barely register — raise
  the passes-per-frame and capture for longer.
- **Receive only.** It cannot decode anything, identify a device by name, or
  tell two identical LED controllers apart.

For actual dBm you need a real spectrum analyser or a TinySA. What this tool
is genuinely good at is the question you actually asked: **which box in my room
is polluting which channels** — and Compare mode answers that well.
