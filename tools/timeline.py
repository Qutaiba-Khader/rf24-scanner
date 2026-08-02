#!/usr/bin/env python3
"""
Timeline of captures - what was powered on, when, and what the radio saw.

This is how a device gets a NAME. The nRF24 can only report "something is on
this frequency"; it can never read an identity off the air. But if you record
what YOU switched on and off, and when, the frequencies that move with it are
that device's - and the name comes from your own note.

    python tools/scan.py --seconds 25 --label "FancyLEDs box OFF"
    python tools/scan.py --seconds 25 --label "FancyLEDs box ON"
    python tools/timeline.py show
    python tools/timeline.py diff 3 4
    python tools/timeline.py md          # write TIMELINE.md next to the captures

Entries live in <captures>/timeline.json, appended in order, never rewritten.
"""

import json
import os
import sys
import time
from pathlib import Path

NCH = 126
MHZ = lambda c: 2400 + c

BANDS = [
    ("Wi-Fi ch 1", 2, 22), ("Wi-Fi ch 6", 27, 47), ("Wi-Fi ch 11", 52, 72),
    ("Wi-Fi ch 13", 62, 82), ("Bluetooth", 2, 80),
    ("BLE adv 37", 1, 3), ("BLE adv 38", 25, 27), ("BLE adv 39", 79, 81),
]


def default_dir():
    return Path(os.path.expanduser("~")) / "OneDrive" / "Desktop" / "rf24-captures"


def tl_path(d=None):
    return Path(d or default_dir()) / "timeline.json"


def load(d=None):
    p = tl_path(d)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def bands_at(ch):
    return sorted({n for n, lo, hi in BANDS if lo <= ch <= hi})


def append(mean, sweeps, label, capture_file, d=None):
    """Record one capture. Never rewrites earlier entries."""
    d = Path(d or default_dir())
    d.mkdir(parents=True, exist_ok=True)
    entries = load(d)
    inband = sum(mean[2:81]) / 79
    floor = sorted(mean[84:])[len(mean[84:]) // 2]
    top = sorted(range(NCH), key=lambda i: -mean[i])[:5]
    entries.append({
        "n": len(entries) + 1,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "label": label or "(unlabelled)",
        "file": Path(capture_file).name if capture_file else None,
        "sweeps": sweeps,
        "inband_avg": round(inband, 2),
        "noise_floor": round(floor, 2),
        "top": [{"ch": c, "mhz": MHZ(c), "pct": round(mean[c], 1)} for c in top if mean[c] >= 1],
        "mean": [round(v, 2) for v in mean],
    })
    tl_path(d).write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return len(entries)


def show(d=None):
    e = load(d)
    if not e:
        print("No captures recorded yet.")
        return 0
    print(f"{'#':>2}  {'time':<19}  {'sweeps':>6}  {'in-band':>7}  {'floor':>5}  label")
    print("-" * 92)
    for x in e:
        print(f"{x['n']:>2}  {x['at']:<19}  {x['sweeps']:>6}  "
              f"{x['inband_avg']:>6.2f}%  {x['noise_floor']:>4.1f}%  {x['label']}")
    print("\nCompare any two:  python tools/timeline.py diff <A> <B>")
    return 0


def diff(a, b, d=None):
    e = {x["n"]: x for x in load(d)}
    if a not in e or b not in e:
        print(f"No such entries. Have: {sorted(e)}")
        return 1
    A, B = e[a], e[b]
    ma, mb = A["mean"], B["mean"]
    print(f"A #{a}  {A['label']}   ({A['at']}, {A['sweeps']} sweeps)")
    print(f"B #{b}  {B['label']}   ({B['at']}, {B['sweeps']} sweeps)")
    print()

    deltas = sorted(((mb[c] - ma[c], c) for c in range(NCH)), reverse=True)

    # A real emitter shows up as ADJACENT channels moving together. Scattered
    # single channels are noise, however large one of them looks.
    risen = {c for v, c in deltas if v >= 1.5}
    runs, cur = [], []
    for c in range(NCH):
        if c in risen:
            cur.append(c)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    clusters = [r for r in runs if len(r) >= 3]

    print("TOP 12 CHANNELS HIGHER IN B")
    for v, c in deltas[:12]:
        tag = ", ".join(bands_at(c)) or ""
        mark = "  <= CLUSTER" if any(c in r for r in clusters) else ""
        print(f"  {MHZ(c)} MHz  ch{c:<4} {ma[c]:5.1f}% -> {mb[c]:5.1f}%  {v:+5.1f}  {tag}{mark}")

    up = sum(1 for v, _ in deltas if v > 0)
    print(f"\nchannels up: {up}/126   down: {126-up}/126   (pure noise sits near 63/63)")

    if clusters:
        print("\nCONTIGUOUS CLUSTERS (this is what a real transmitter looks like)")
        for r in clusters:
            lo, hi = MHZ(r[0]), MHZ(r[-1])
            avg = sum(mb[c] - ma[c] for c in r) / len(r)
            print(f"  {lo}-{hi} MHz  ({len(r)} channels, average {avg:+.1f})  "
                  f"{', '.join(bands_at(r[len(r)//2])) or 'no standard protocol here'}")
        print(f"\n  => attribute this to: {B['label']}")
    else:
        print("\nNo contiguous cluster. Nothing here is distinguishable from noise -")
        print("the device is not transmitting on 2.4 GHz, is too far from the scanner,")
        print("is below -64 dBm, or only transmits in bursts.")
    return 0


def write_md(d=None):
    d = Path(d or default_dir())
    e = load(d)
    out = ["# rf24scan timeline", "",
           "What was powered on, when, and what the radio measured.",
           "Names come from the labels; frequencies come from the radio.", "",
           "| # | time | label | sweeps | in-band | floor | busiest |",
           "|---|------|-------|--------|---------|-------|---------|"]
    for x in e:
        top = ", ".join(f"{t['mhz']}MHz {t['pct']}%" for t in x["top"][:3]) or "-"
        out.append(f"| {x['n']} | {x['at']} | {x['label']} | {x['sweeps']} | "
                   f"{x['inband_avg']}% | {x['noise_floor']}% | {top} |")
    p = d / "TIMELINE.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Written: {p}")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "show"
    if cmd == "show":
        return show()
    if cmd == "md":
        return write_md()
    if cmd == "diff" and len(argv) >= 4:
        return diff(int(argv[2]), int(argv[3]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
