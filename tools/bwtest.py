#!/usr/bin/env python3
"""
Bandwidth sweep - turn the 1-bit detector into a shape *and* strength probe.

    python tools/bwtest.py --port COM19 --seconds 60

WHY THIS WORKS
--------------
The nRF24's RPD is one bit at a fixed -64 dBm. It can never say "how strong".
But the air data rate sets the *receiver's IF bandwidth*, and that changes how
much of a signal's power lands inside the receiver:

    250 kbps -> narrowest        1 Mbps -> ~1 MHz        2 Mbps -> ~2 MHz

- A **wideband** source (a 20 MHz Wi-Fi carrier) spreads its power across the
  band, so a wider receiver collects more of it. Roughly +3 dB from 1 to 2 Mbps.
- A **narrowband** source (an LED remote, a dongle, a fixed carrier) fits
  entirely inside every setting, so it delivers the *same* power at all three.

Capturing the same scene at all three bandwidths therefore answers two
questions the scanner otherwise cannot:

1. **Shape.** Occupancy climbs with bandwidth -> wideband. Flat -> narrowband.
2. **Strength**, which is the one actually blocking this project. If a channel
   reads 39% at every bandwidth, the source is comfortably *above* -64 dBm when
   it transmits and that 39% is a true duty cycle. If the reading climbs with
   bandwidth, the source is sitting *near* the threshold and the percentage is
   partly a measurement artefact.

Both conclusions are relative and coarse. This is not a power meter. But it
separates "loud and intermittent" from "marginal and constant", and those two
have completely different implications for whether a device can break a link.

HOW IT CAPTURES
---------------
By default it uses the firmware's **cycle mode** (`B3`, v1.2.0+): the board
rotates the bandwidth every frame and tags each one, so all three are measured a
few hundred milliseconds apart. That matters - taking three separate one-minute
captures assumes the room holds still for three minutes, and in this room the
Xbox alone moves a band by +6 to +30 depending on whether it is in use. A change
like that would masquerade as a bandwidth trend.

`--sequential` falls back to three separate captures for firmware older than
v1.2.0.

RULES (same as every other capture in this project)
- Do not move the scanner. You would be measuring your own hand.
- 60 seconds minimum per bandwidth. Shorter runs invent clusters.
- Change nothing else in the room while it runs.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan import NCH, MHZ, bands_at, collect, find_port, mean_of  # noqa: E402

RATE_NAMES = ("250kbps", "1Mbps", "2Mbps")

# A channel has to be doing *something* at some bandwidth before its trend is
# worth reading. Below this the trend is just noise on noise.
FLOOR = 5.0

# Contiguous runs are what a real transmitter looks like; scattered channels are
# noise. Same rule the timeline differ uses.
MIN_RUN = 3


def clusters(mark):
    """Contiguous runs of True in `mark`, as (lo, hi) inclusive."""
    out, start = [], None
    for c in range(NCH):
        if mark[c] and start is None:
            start = c
        elif not mark[c] and start is not None:
            if c - start >= MIN_RUN:
                out.append((start, c - 1))
            start = None
    if start is not None and NCH - start >= MIN_RUN:
        out.append((start, NCH - 1))
    return out


def verdict(narrow, mid, wide):
    """Classify one cluster from its three bandwidth means."""
    if wide < FLOOR and mid < FLOOR:
        return "too quiet to classify", ""

    rise = wide - narrow
    # Relative rise matters more than absolute once a channel is already busy:
    # a source pinned at 95% cannot climb, however wideband it is.
    headroom = max(1.0, 100.0 - narrow)
    frac = rise / headroom

    if narrow >= 85 and wide >= 85:
        return ("STRONG, saturated",
                "trips the detector at every bandwidth - well above -64 dBm")
    if frac >= 0.25:
        return ("WIDEBAND and marginal",
                "climbs sharply with receiver bandwidth: energy is spread wide "
                "AND sitting near the -64 dBm threshold")
    if frac >= 0.10:
        return ("WIDEBAND",
                "collects more power in a wider receiver - a 20 MHz carrier, "
                "i.e. Wi-Fi or similar")
    if abs(rise) < 3:
        return ("NARROWBAND and strong",
                "identical at every bandwidth: all its power already fits in "
                "the narrowest receiver, and it is above threshold")
    if rise < -3:
        return ("NARROWBAND",
                "reads LOWER in a wider receiver - the extra bandwidth added "
                "noise, not signal")
    return ("mixed / inconclusive", "no clear trend - repeat the run")


def analyse(means, seconds, sweeps):
    m0, m1, m2 = means
    busy = [max(m0[c], m1[c], m2[c]) >= FLOOR for c in range(NCH)]
    cls = clusters(busy)

    print()
    print("=" * 74)
    print("BANDWIDTH SWEEP")
    print("=" * 74)
    for i, n in enumerate(RATE_NAMES):
        print(f"  {n:<9} {sweeps[i]:>4} sweeps over {seconds}s")

    ctrl = [sorted(m[84:])[len(m[84:]) // 2] for m in means]
    print(f"\n  control zone (above 2484 MHz, should be ~0):  "
          f"{ctrl[0]:.1f}% / {ctrl[1]:.1f}% / {ctrl[2]:.1f}%")
    if max(ctrl) > 15:
        print("  WARNING: the receiver is being overloaded. Move it away and "
              "re-run;\n           every number below reads too high.")

    if not cls:
        print(f"\nNothing reached {FLOOR:.0f}% at any bandwidth. Either the room is "
              f"quiet,\nor everything here is below the -64 dBm floor - move the "
              f"scanner\nto within 10-20 cm of the suspect and repeat.")
        return

    print(f"\n{'BAND':<22}{'250kbps':>9}{'1Mbps':>9}{'2Mbps':>9}{'RISE':>8}   VERDICT")
    print("-" * 74)
    rows = []
    for lo, hi in cls:
        n = hi - lo + 1
        a = sum(m0[lo:hi + 1]) / n
        b = sum(m1[lo:hi + 1]) / n
        c = sum(m2[lo:hi + 1]) / n
        v, why = verdict(a, b, c)
        span = f"{MHZ(lo)}-{MHZ(hi)} MHz"
        print(f"{span:<22}{a:8.1f}%{b:8.1f}%{c:8.1f}%{c-a:+7.1f}   {v}")
        rows.append({"lo": lo, "hi": hi, "mhz": span,
                     "bw250": round(a, 1), "bw1M": round(b, 1),
                     "bw2M": round(c, 1), "rise": round(c - a, 1),
                     "verdict": v, "why": why,
                     "protocols": bands_at((lo + hi) // 2)})

    print()
    for r in rows:
        print(f"{r['mhz']}  (ch {r['lo']}-{r['hi']})")
        print(f"   {r['verdict']}: {r['why']}")
        if r["protocols"]:
            print(f"   overlaps: {', '.join(r['protocols'])}")
        print()

    print("HOW TO READ THIS")
    print("  Occupancy that CLIMBS with bandwidth  -> the source is wideband,")
    print("     and/or it is hovering near the -64 dBm threshold.")
    print("  Occupancy that stays FLAT             -> the source is narrowband,")
    print("     and it is comfortably above threshold when it transmits, so the")
    print("     percentage is a genuine duty cycle rather than an artefact.")
    print("  This is coarse and relative. It is not a power measurement.")
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="Capture the same scene at all three receiver bandwidths.")
    ap.add_argument("--port")
    ap.add_argument("--seconds", type=int, default=60,
                    help="per bandwidth (default 60 - do not go lower)")
    ap.add_argument("--sequential", action="store_true",
                    help="three separate captures instead of interleaving. "
                         "Needed on firmware older than v1.2.0, which has no "
                         "cycle mode. Assumes the room holds still throughout.")
    ap.add_argument("--save", metavar="FILE", help="write the result as JSON")
    ap.add_argument("--label", metavar="TEXT", default="",
                    help="what was powered on during this run")
    args = ap.parse_args()

    if args.seconds < 30:
        print(f"WARNING: {args.seconds}s per bandwidth is short. 25-second runs "
              f"manufactured\nfalse clusters twice in this project. 60s is the "
              f"documented minimum.\n")

    port = find_port(args.port)

    if args.sequential:
        print("Sequential mode: three separate captures.")
        print("Do not move the scanner, and change nothing in the room.\n")
        means, sweeps = [], []
        for rate in (0, 1, 2):
            print(f"--- {RATE_NAMES[rate]} ({args.seconds}s) ---")
            frames, control = collect(port, args.seconds, bw=rate)
            if not frames:
                sys.exit("No sweeps received. "
                         + ("; ".join(control) or "board silent."))
            got = [c for c in control if "rate=" in c]
            if got:
                print(f"  board confirms: {got[-1]}")
            means.append(mean_of(frames))
            sweeps.append(len(frames))
            time.sleep(1.0)
        total = args.seconds
    else:
        # Cycle mode. The firmware rotates the bandwidth every frame and tags
        # each one, so all three are measured a few hundred ms apart instead of
        # a minute apart. That matters here: the Xbox alone moves a band by +6
        # to +30 depending on whether it is in use, which would show up as a
        # bandwidth trend that is really just the room changing.
        total = args.seconds * 3
        print(f"Cycle mode: one capture of {total}s, bandwidth rotating per frame.")
        print("Do not move the scanner.\n")
        frames, control = collect(port, total, bw=3)
        if not frames:
            sys.exit("No sweeps received. " + ("; ".join(control) or "board silent."))
        if not any("rate=cycle" in c for c in control):
            sys.exit(
                "The board did not accept B3 (cycle).\n"
                "That command needs firmware v1.2.0 or newer - check the banner\n"
                "above. Either flash v1.2.0, or re-run with --sequential to do\n"
                "three separate captures on the firmware you have.")
        buckets = [[], [], []]
        untagged = 0
        for f in frames:
            if f.get("rate") is None:
                untagged += 1
            else:
                buckets[f["rate"]].append(f)
        if untagged:
            sys.exit(f"{untagged} frames carried no bandwidth tag - the firmware "
                     f"is older than v1.2.0.\nRe-run with --sequential.")
        if min(len(b) for b in buckets) < 5:
            sys.exit("Too few frames per bandwidth (%s). Run longer."
                     % ", ".join(str(len(b)) for b in buckets))
        means = [mean_of(b) for b in buckets]
        sweeps = [len(b) for b in buckets]

    rows = analyse(means, total if args.sequential else args.seconds, sweeps)

    if args.save:
        json.dump({"label": args.label, "seconds": args.seconds,
                   "sweeps": sweeps, "rates": list(RATE_NAMES),
                   "mode": "sequential" if args.sequential else "cycle",
                   "means": means, "clusters": rows},
                  open(args.save, "w"), indent=1)
        print(f"\nSaved: {args.save}")


if __name__ == "__main__":
    main()
