#!/usr/bin/env python3
"""
Write RESULTS.md - every finding in plain tables, nothing else.

    python tools/results_md.py
"""

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report      # noqa: E402
import timeline    # noqa: E402
import build_report  # noqa: E402

MHZ = report.MHZ


def occupancy(mean, lo, hi):
    return sum(mean[c] for c in range(lo, hi + 1)) / (hi - lo + 1)


# Wi-Fi 2.4 GHz channels are 20 MHz wide: ch k is centred on 2412+5(k-1) MHz,
# which in nRF channel numbers is 12+5(k-1), spanning +/-10.
WIFI = {k: (12 + 5 * (k - 1) - 10, 12 + 5 * (k - 1) + 10) for k in range(1, 14)}


def wifi_at(c):
    """Which Wi-Fi channels overlap this nRF channel."""
    return [k for k, (lo, hi) in WIFI.items() if lo <= c <= hi]


def wifi_str(lo, hi):
    ks = sorted({k for c in range(lo, hi + 1) for k in wifi_at(c)})
    return ", ".join(str(k) for k in ks) if ks else "-"


def bt_note(lo, hi):
    """Bluetooth hops 2402-2480 = nRF 2..80. How much of that this blocks."""
    overlap = len([c for c in range(lo, hi + 1) if 2 <= c <= 80])
    return f"{overlap} of 79" if overlap else "outside BT"


def main():
    entries = {x["n"]: x for x in timeline.load()}
    if not entries:
        sys.exit("No captures yet.")
    m = {n: entries[n]["mean"] for n in entries}

    # Reuse exactly the same attribution logic the HTML report uses, so the two
    # can never disagree.
    trials, suspects = build_report.build_trials(m)
    atts = [report.attribute(name, runs) for name, runs in trials]

    L = []
    L.append("# RF scan results")
    L.append("")
    L.append("Everything found by switching each device off and on while scanning.")
    L.append("")

    # ---------------------------------------------------------------- devices
    latest_mean = m[max(entries)]
    L.append("## 1. Devices identified")
    L.append("")
    L.append("| Device | Frequency band | nRF channels | Width | Wi-Fi ch | "
             "Blocks | Adds | Peak seen | Confidence | Trials |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for a in atts:
        if a["verdict"] not in ("confirmed", "weak") or not a["bands"]:
            continue
        b = max(a["bands"], key=lambda z: z["avg"])
        lo, hi = b["lo"], b["hi"]
        peak = max(latest_mean[c] for c in range(lo, hi + 1))
        rows.append((
            b["avg"], a["name"],
            f"{MHZ(lo)}-{MHZ(hi)} MHz",
            f"ch {lo}-{hi}",
            f"{hi - lo + 1} ch ({hi - lo + 1} MHz)",
            wifi_str(lo, hi),
            bt_note(lo, hi),
            f"+{b['avg']:.0f}",
            f"{peak:.0f}%",
            f"{a['confidence']}%",
            str(a["runs"]),
        ))
    for r in sorted(rows, reverse=True):
        L.append("| **" + r[1] + "** | " + " | ".join(r[2:8]) + " | " +
                 r[8] + " | **" + r[9] + "** | " + r[10] + " |")
    L.append("")
    L.append("- **nRF channels** are what the scanner counts: channel N = 2400+N MHz.")
    L.append("- **Blocks** = how many of the 79 channels Bluetooth hops through are lost.")
    L.append("- **Adds** = extra occupancy measured when that device is switched on.")
    L.append("- **Peak seen** = the highest single-channel reading in that band.")
    L.append("")

    # ------------------------------------------------- channel-by-channel map
    named = report.named_channels(atts)
    L.append("### Channel-by-channel (everything above 5% busy)")
    L.append("")
    L.append("| MHz | nRF ch | Wi-Fi ch | Busy | Who is there |")
    L.append("|---|---|---|---|---|")
    for c in range(126):
        if latest_mean[c] < 5.0:
            continue
        owners = " + ".join(n.split("(")[0].strip() for n, _v, _a in named.get(c, []))
        for s in suspects:
            if s["lo"] <= c <= s["hi"]:
                owners = (owners + " + " if owners else "") + "unidentified"
        w = ", ".join(str(k) for k in wifi_at(c)) or "-"
        L.append(f"| {MHZ(c)} | {c} | {w} | {latest_mean[c]:.0f}% | {owners or 'not identified'} |")
    L.append("")

    # --------------------------------------------------------------- suspects
    if suspects:
        L.append("## 2. Still unidentified")
        L.append("")
        L.append("| Frequency band | nRF channels | Wi-Fi ch | Busy | Suspects to test next |")
        L.append("|---|---|---|---|---|")
        for s in suspects:
            cands = ", ".join(c.split("(")[0].strip() for c in s["candidates"])
            L.append(f"| {MHZ(s['lo'])}-{MHZ(s['hi'])} MHz | ch {s['lo']}-{s['hi']} | "
                     f"{wifi_str(s['lo'], s['hi'])} | {s['level']:.0f}% | {cands} |")
        L.append("")

    # -------------------------------------------------------------- retracted
    bad = [a for a in atts if a["verdict"] not in ("confirmed", "weak")]
    if bad:
        L.append("## 3. Tested and cleared")
        L.append("")
        L.append("| Device | Result |")
        L.append("|---|---|")
        for a in bad:
            L.append(f"| {a['name']} | {a['why']} |")
        L.append("")

    # --------------------------------------------------------------- timeline
    L.append("## 4. Every capture, in order")
    L.append("")
    L.append("| # | Time | What was powered on | Scans | Band avg |")
    L.append("|---|------|---------------------|-------|----------|")
    for n in sorted(entries):
        e = entries[n]
        L.append(f"| {n} | {e['at'][11:]} | {e['label']} | {e['sweeps']} | {e['inband_avg']}% |")
    L.append("")

    # ------------------------------------------------------------ what it means
    latest = m[max(entries)]
    a = report.analyse(latest)
    L.append("## 5. What it means")
    L.append("")
    L.append("| Measure | Value | Meaning |")
    L.append("|---|---|---|")
    L.append(f"| Bluetooth channels still clear | **{a['usable']} of {a['total']}** | "
             f"room your buds have left to hop into |")
    L.append(f"| Noise floor (nRF ch 84-125, above the ISM edge) | {a['floor']:.1f}% | "
             f"0% means the readings are trustworthy, not overloaded |")
    L.append("| Your buds' link | **2458-2466 MHz** (ch 58-66) | Wi-Fi ch 11 space |")
    L.append("| The crowding | **2414-2430 MHz** (ch 14-30) | Wi-Fi ch 1 space |")
    L.append(f"| Quietest 3 MHz window | around 2434-2450 MHz | least contested part of the band |")
    L.append("")
    L.append("The Xbox and the FancyLEDs box do **not** sit on the same frequencies as")
    L.append("your buds. They take 17 of the 79 channels Bluetooth needs to hop through,")
    L.append("which shrinks the pool your buds can move around in.")
    L.append("")
    L.append("## 6. What to do")
    L.append("")
    L.append("| Priority | Action |")
    L.append("|---|---|")
    L.append("| 1 | Move the **transmitter** away from the Xbox / LED corner |")
    L.append("| 2 | Or move the **Xbox and FancyLEDs box** away from where you listen |")
    L.append("| 3 | Identify the last unknown source at 2417-2428 MHz |")
    L.append("")
    L.append("Distance is what works. The LED box was invisible from across the room and")
    L.append("obvious at 10 cm - the same falloff protects your earbuds.")

    out = pathlib.Path(os.path.expanduser("~")) / "OneDrive" / "Desktop" / "RESULTS.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Written: {out}")
    print()
    print("\n".join(L[:28]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
