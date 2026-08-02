# Diagnosis — why the earbuds drop out

Everything measured and reasoned on 2026-08-02, in the order it matters.
The raw results live in [`FINDINGS.md`](FINDINGS.md); this file is the
interpretation.

---

## 1. The symptom, stated precisely

The problem **only happens in gaming mode or in Auracast.** Normal music
playback is fine, in the same room, at the same time, with the same devices
switched on.

Two more facts that narrow it hard:

- The transmitter sits **under 1 m** from the earbuds.
- **More than 7 different transmitters** were tried. All show the same failure.

Any explanation has to fit all three. "There is interference in the room" on its
own does not — the room is identical when normal playback works.

---

## 2. The gear

| | |
|---|---|
| **Earbuds** | Galaxy Buds 3 Pro — LC3, LE Audio, Auracast. **No aptX.** |
| **Transmitter** | FlooGoo **FMA121** — BT 5.4, **Class 1, +15 dBm**, USB-C + 3.5 mm in |
| | Codecs: SBC, LC3, aptX family (unusable here — the buds have no aptX) |
| | Modes: Auracast broadcast (PBP/TMAP), unicast music (MCP) + voice (CCP) |
| | Gaming mode with voice back channel, **15–25 ms** |
| | Config app: **FlooCast** — Windows build on the Microsoft Store |
| **Router** | Wi-Fi **channel 11, 20 MHz, TX power Low** — unchanged throughout |

FlooCast v1.1.5 added FMA121 support **and a latency adjustment option in
broadcast mode**; v1.1.7 added broadcast volume and quality controls. That
latency control is the only meaningful knob on this dongle — see §6.

---

## 3. What the band actually looks like

Measured at the listening position, 12 captures.

| Range | Occupancy | |
|---|---|---|
| 2400–2414 MHz | < 1.5% | clean |
| **2415–2429 MHz** | **21 – 41%** | **the problem** |
| 2430–2483 MHz | < 3% | clean |
| 2432–2456 MHz | < 1% | quietest stretch in the room |

The busy block is 15 MHz wide, flat-topped, and **present in every single
capture** — including ones with the FancyLEDs box, the Xbox and the earbuds all
switched off. Edges wander to 2414–2430; the core is 2415–2429.

### What that costs each protocol

| Path | Channel scheme | Lost inside 2415–2429 |
|---|---|---|
| Buds on **Classic A2DP** (normal music) | 79 × 1 MHz, 2402–2480 | BT ch 13–27 → **15 of 79 (19%)** |
| Buds + FMA121 on **LE Audio** (LC3, gaming, Auracast) | 40 × 2 MHz | data ch 6–11 → **6 of 37 (16%)** |
| **plus, on both** | | **advertising channel 38** |

### The single worst channel: 2426 MHz

It reads **41.1%** — the highest number in the entire scan. **2426 MHz is BLE
advertising channel 38**, one of only three channels used to discover a
broadcast and to re-establish a link.

That is why the failure presents as *"it drops and takes a while to come back"*
rather than a brief crackle. Losing a data channel costs a packet. Losing an
advertising channel costs the reconnection.

---

## 4. Who owns 2415–2429 — still unknown

**It is not the router.** The router is on channel 11 = 2452–2472 MHz, which
measured **0.5% average / 2.7% peak**. Its own transmissions are below the
scanner's −64 dBm floor and never appear at all. Those settings were unchanged
before, during and after every capture, so nothing in the data is explained by a
configuration change. Channel 11 / 20 MHz / Low is correct — leave it alone.

**It is not the devices we power-cycled either.** They only change its *level*,
never its centre frequency:

| Capture | State | 2415–2429 | Centre |
|---|---|---|---|
| `07` | FancyLEDs **off** | 37–40% | 2422 |
| `06` | FancyLEDs **on** | 56–58% | 2422 |
| `09` | Xbox **off** | 21% | 2422 |
| `10` | Xbox **on** | 27% | 2422 |
| `12` | buds + TX **off** | 39% | 2422 |

Flat top, ~20 MHz occupied bandwidth, never off, centre pinned at **2422 MHz =
Wi-Fi channel 3**. That is an access-point beacon.

So the earlier attribution was too generous: **FancyLEDs (99%) and the Xbox
(98%) are contributors to that block, not its owner.** They are clients of
whatever access point is on channel 3 — and since the main router is on 11, that
is not the main router.

**Untested candidates, in order:**

1. A second router / ISP modem-router / mesh node / range extender
2. The Android TV box or the TV raising a Wi-Fi Direct or Cast group
3. A neighbour's access point

Fastest way to settle it: the router's **Nearby Networks / Site Survey** page, or
any WiFi analyzer app on a phone. Anything on channel 1–3 is the suspect. If the
SSID is yours, there is a second box running.

---

## 5. Why it only breaks in gaming mode and Auracast

This is the part that explains the symptom, and it is about **margin**, not about
interference level. The interference is identical in all three cases.

| Protection | Normal A2DP | Gaming mode | Auracast (BIS) |
|---|---|---|---|
| **Buffer** | 150–200 ms | shrunk to hit 15–25 ms | tiny |
| **Retransmission** | acknowledged, retry until it lands | acknowledged, but almost no time to fit a retry | **none — no acknowledgement at all** |
| **AFH** | buds report bad channels back, source stops using them | same | **no feedback path exists** |
| **Result** | 19% channel loss absorbed silently | every collision surfaces | unrecoverable losses |

- **Normal playback** has all three protections. Your 15 lost channels vanish
  into the buffer and you never hear them.
- **Gaming mode** removes the buffer. Samsung documents the trade directly: game
  mode reduces buffer size for speed, which makes the connection less stable. The
  interference did not change — the margin hiding it did.
- **Auracast is a Broadcast Isochronous Stream.** It is one-way and
  connectionless. There is no acknowledgement, so the buds cannot ask for a
  retry; retransmission is a **fixed pre-scheduled repeat count (RTN)** sent
  whether anyone received it or not. If the repeats land badly, that audio is
  simply gone.

### Why 7 different transmitters all failed

Bluetooth's Broadcast Audio Profile defines its configurations in **two tables —
Low Latency and High Reliability** — differing by **RTN** and by max transport
latency. A dongle sold as a low-latency gaming/TV transmitter defaults to the
**Low Latency** table.

In Auracast the **transmitter alone decides RTN and the buds have no vote**,
because there is no return path. Seven vendors, seven chipsets, one shared
default. That fits the evidence without blaming the earbuds, which support LC3,
LE Audio and Auracast correctly.

---

## 6. What to do, in order

| # | Action | Why |
|---|---|---|
| 1 | **Clean-room test.** Buds + FMA121 under 1 m, somewhere the band is clean, gaming mode and Auracast | Decides room-vs-preset in five minutes and costs nothing. Still stutters → the room is exonerated, stop hunting RF |
| 2 | **FlooCast → raise the broadcast latency / quality setting** | Moves from the Low Latency table to High Reliability — literally more retransmissions per packet. The only safety net Auracast can have |
| 3 | **Try LE Audio unicast (MCP) instead of Auracast** | Gives LC3 *and* acknowledged retransmission, which broadcast structurally cannot |
| 4 | **Identify and kill the channel-3 emitter** | 15 lost channels is survivable with a buffer. It is not survivable with a fixed low RTN |
| 5 | **Get the Xbox off 2.4 GHz** — Ethernet or 5 GHz | Worth +30 in use / +6 idle. The variability matches dropouts that come and go |
| 6 | If gaming mode is for TV lip-sync, use the **TV's audio-delay offset** instead | Fixes sync without shrinking the buffer |

Robustness ladder, best first:

1. **LE Audio unicast (LC3) with a High Reliability QoS** — LC3 quality *and* a real retry path
2. Auracast with **High Reliability**
3. Gaming mode — acknowledged, but 15–25 ms to fit a retry in
4. Auracast with **Low Latency** — likely where the setup is now

---

## 7. The limit of this instrument — read before concluding anything

The nRF24's RPD is a **1-bit detector with a fixed −64 dBm threshold**. It
reports *above* or *below* and nothing else.

- The FMA121 is **+15 dBm** at under 1 m, which puts roughly **−19 dBm** into the
  earbuds.
- BLE needs about **21 dB** of carrier-to-interference to survive a co-channel
  hit.
- If the 2415–2429 block is near −64 dBm at your ears, there is ~45 dB of margin
  and **the room should not be able to break this link**.
- If it is nearer −30 dBm, it certainly can.

**This scanner cannot distinguish those two cases.** That is why step 1 above is
a physical test, not another capture. Every occupancy number in this project is a
duty cycle, never a power level.

---

## 8. Open questions

- **Who owns 2415–2429 MHz?** Untested: Android TV box, TV, second AP, neighbour.
- **Does the clean-room test still stutter?** Not yet run. This is the decisive one.
- **Does raising RTN in FlooCast fix Auracast?** Not yet tried.
- **The mouse dongle's own frequency** was never identified — it is the second
  victim in the original brief and has had no capture pair of its own.
