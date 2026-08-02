# Findings, bugs, and what still needs fixing

Session of 2026-08-02: built the scanner, took it from "does not enumerate" to
real measurements, and identified the interferers.

---

## 1. The results

### Devices identified — by switching each one off and on while scanning

| Device | Frequency band | nRF ch | Width | Wi-Fi ch | Steals | Adds | Confidence | Trials |
|---|---|---|---|---|---|---|---|---|
| **FancyLEDs box (Tuya)** | 2414–2430 MHz | ch 14–30 | 17 ch | 1–6 | **17 of 79** | +22 | **99%** | 2 |
| **Xbox** | 2414–2430 MHz | ch 14–30 | 17 ch | 1–6 | **17 of 79** | +18 avg | **98%** | 2 |
| Galaxy Buds 3 Pro + transmitter | 2458–2466 MHz | ch 58–66 | 9 ch | 9–13 | 9 of 79 | +5 | 53% | 1 |
| Unidentified | 2417–2428 MHz | ch 17–28 | 12 ch | 1–6 | 12 of 79 | — | untested | 0 |

The Xbox figure is an average of two trials that differed 5×: **+30 when in use,
+6 when idle**. That variability matches dropouts that come and go.

### The picture

```
                         ╔══ COLLISION — 17 of 79 channels taken ══╗
                         ║                                          ║
 Buds need ALL of this   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
                         2402 ─────────────────────────────────── 2480

 FancyLEDs (Tuya)        ██████████ 2414-2430          +22   99%
 Xbox                    ██████████ 2414-2430          +18   98%
 Unidentified              ███████  2417-2428           21%  untested
 Galaxy Buds                                   ███ 2458-2466   53%

 2400   2410   2420   2430   2440   2450   2460   2470   2480 MHz
```

### What it means

The buds sit **28 MHz clear** of the interferers — this is **not** a frequency
clash. Bluetooth hops across all 79 channels 1600 times a second, and the Xbox
and LED box sit *inside* that hop range. Every pass through ch 14–30 lands on
them, AFH blacklists those channels, and the buds end up working with ~62
instead of 79. Fewer channels → more retries → the dropouts.

### Measured at the listening position
Even at the buds' own location, 2415–2429 still reads **36–41%**. The
interference reaches where it matters.

---

## 2. The bug that cost the session

### `@micropython.native` on `sweep_pass()`

From the MicroPython docs
([speed_python.html](https://docs.micropython.org/en/latest/reference/speed_python.html)):

> *"The background scheduler is **not run** during execution of native code."*

The whole 126-channel sweep ran inside a native function, so **TinyUSB was
suspended for every pass**. The board enumerated at boot, then died the instant
scanning began — Windows reported `Unknown USB Device (Device Descriptor Request
Failed)` and no COM port ever appeared.

Present since **v1.0.0**. Found after **eleven releases**.

**Why it hid so well**
- Stock MicroPython enumerated perfectly on the same board with the same radio,
  because it never sweeps. That looked like proof the hardware was fine and the
  firmware was at fault — correct, but it pointed at the wrong line.
- Adding per-channel `sleep_ms(1)` yields did nothing, because **the scheduler
  does not run inside native code at all**. The fix could not work while the
  decorator was there.
- No `S` frame was ever received on *any* version, on any host, at any point.

It bought almost nothing: the 200 µs PLL dwell dominates that loop. The module
docstring said as much while the decorator sat above the function.

**Guard added:** CI now fails on `@micropython.native` or `@micropython.viper`
anywhere in the firmware (`tools/check_firmware.py`, negative-tested).

---

## 3. Every other firmware bug (all self-inflicted)

| # | Bug | Symptom | Fix |
|---|---|---|---|
| 1 | `say()` called **itself** — a global replace of `sys.stdout.write(` → `say(` also rewrote the call inside `say()`'s own body | Infinite recursion on every write; shipped in v1.0.3 **and** v1.0.4 | Restored the real write; CI fails on any self-recursive function |
| 2 | `while poller.poll(0)` tested **truthiness** | With no host, CDC reports POLLHUP/POLLERR — a *non-empty* list — so `sys.stdin.read(1)` blocked forever | Check the `POLLIN` bit before reading |
| 3 | `select.poll()` registered on **`sys.stdout`** for POLLOUT | Unsupported on MicroPython: `register()` succeeds, then `poll(0)` **hangs**. A hang is not an exception, so the try/except caught nothing | Removed; big DO-NOT-REINTRODUCE comment left in place |
| 4 | UF2 magic check compared `55463200` | `0x0A324655` is `55 46 32 0A` on disk. The `.uf2` was fine; **the checker was wrong** | Compare the full word, print what was actually seen |
| 5 | Blocking CDC write with nothing draining | Board stalls its own USB stack; next port open hangs | Halt the board before disconnecting (CLI and web tool both) |
| 6 | LED went **dark** when the radio was missing | Indistinguishable from a crash — and that is exactly how it was read | Slow 1/sec blink = alive but no radio; dark = firmware dead |
| 7 | **Unversioned release asset names** | Browser saved every download as `rf24scan-pico.uf2`, so the user re-flashed **v1.0.0 nine times** while I debugged a build that never had the fixes | Releases now also ship `rf24scan-pico-vX.Y.Z.uf2`; the web tool warns on stale firmware |

---

## 4. Web tool bugs found

| Bug | Consequence |
|---|---|
| `$('btnExpCmp')` vs markup `btnExportCmp` | A null-deref at the **top level of a classic script aborts the rest of the body** — one typo silently killed every feature initialised after it |
| `log()` did `textContent +=` plus a `scrollTop` read per line | O(n²) with a forced synchronous layout every line; a fast serial stream locks the tab |
| `named_channels()` kept only the **strongest** device per channel | Silently deleted the other device from the table — looked like it had been removed from the report |
| Spike detection used a **ratio** against local background | Occupancy caps at 100%, so a gadget on a 45%-busy carrier can never reach 2.2× it. **The loudest thing in the room went unreported** |
| A uniformly busy band collapsed into one 80-channel "plateau" | Swallowed the narrowband culprit entirely |
| Compare classifier used `maxCh − minCh` | One marginal outlier turned an obvious narrowband spike into "scattered/hopping" — a **wrong diagnosis** |
| `PAD_L` sized for y-axis numbers | Band-lane labels clipped ("BT Classic" → "T Classic") |
| Top-3 direct labels were 3 pixels of one peak | Overprinted into unreadable mush |
| Spike *inside* a Wi-Fi plateau was dropped | That is the most interesting case, not the least |
| BLE advertising channels labelled as mystery devices | Sent you hunting for something that is supposed to be there |

---

## 5. Still to do

### Web tool — it has never been used against real hardware
Everything below was learned on the CLI and has **not** been ported:

| Priority | Change |
|---|---|
| **High** | **Test the web tool against the real board.** All 12 captures were taken with `tools/scan.py`; the browser tool has only ever seen synthetic demo data |
| **High** | Port **cluster detection** — the web compare uses a flat ±6 point threshold. The CLI proved that *contiguous adjacent channels* is the real signal and scattered ones are noise |
| **High** | Port the **confidence percentage** — `(rise / 3-point noise) × √trials`, capped at 99 |
| High | Port **multi-trial replication**. A single trial produced two false positives in this session; the web tool still treats one capture pair as conclusive |
| Medium | Port **labels + timeline** so captures record what was powered on |
| Medium | Port the **interception map** drawing |
| Medium | Warn when a capture is **shorter than 60 s** — 25 s runs manufactured false clusters twice |
| Low | Add a "move the scanner closer" hint when nothing is detected — distance was the whole answer |

### Firmware

| Priority | Change |
|---|---|
| Medium | `BOOT_SETTLE_MS = 3000` is probably unnecessary now the native decorator is gone. Test removing it — it delays every boot by 3 s |
| Medium | Re-check whether the "stay silent until the host speaks" gate is still needed, for the same reason |
| Low | Expose sweep timing (`YIELD_EVERY`) as a runtime command so the USB-vs-speed trade can be tuned without reflashing |
| Low | Consider a `U` command that calls `machine.bootloader()` so updates need no BOOTSEL press |

### Docs

| Priority | Change |
|---|---|
| **High** | `INTERPRETING.md` still leads with **USB 3.0** as the prime suspect. **The user has no USB 3.0 in this setup.** Replace with what was actually measured |
| High | Both docs should say plainly: **take the scanner to within 10–20 cm of the suspect.** That single step is what turned three null results into a 99% identification |
| Medium | Document the −64 dBm floor as an operational rule, not a footnote: *a quiet reading across the room is not evidence of a quiet device* |
| Medium | Add the replication rule: **one trial is never enough** |

### Method (for whoever picks this up)

1. **Take the scanner to the suspect**, 10–20 cm. The RPD floor is a hard −64 dBm.
2. **60 s minimum** (~146 sweeps). 25 s runs produce false clusters.
3. **Contiguous clusters** = a real transmitter. Scattered channels = noise.
4. **Repeat every trial.** Two of this session's findings evaporated on repeat.
5. Never move the scanner between an off and on capture.
6. Confidence caps at 99% — a 1-bit detector proves a signal *follows a power
   switch*, never who emitted it.

---

## 6. Next measurement

Identify the remaining **21% at 2417–2428 MHz**. Suspects in order:

1. Android TV box (Wi-Fi)
2. The TV itself (Wi-Fi)
3. Router / a neighbour's Wi-Fi on channel 1
4. soundcore Liberty 4 NC (paired to this PC, never tested)

```
python tools\scan.py --port COM19 --seconds 60 --label "Android box OFF"
python tools\scan.py --port COM19 --seconds 60 --label "Android box ON"
python tools\timeline.py diff <A> <B>
python tools\build_report.py
```
