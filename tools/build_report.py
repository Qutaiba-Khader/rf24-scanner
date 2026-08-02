#!/usr/bin/env python3
"""
Build the HTML report with device attribution from the timeline.

Names come from the labels you gave each capture; frequencies come from the
radio; confidence comes from whether a cluster REPLICATES across trials.

    python tools/build_report.py
"""

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report      # noqa: E402
import timeline    # noqa: E402


def build_trials(m):
    """Which captures pair up into off/on trials, and what is still unclaimed.

    Kept as one function so the HTML report and RESULTS.md are built from the
    same pairings and can never disagree with each other.
    """
    trials = []

    # FancyLEDs (Tuya) - CLOSE RANGE, scanner 10-20cm from the box.
    #   #7 = box OFF     #6 = box ON      #8 = box ON again (independent repeat)
    # Everything at the same spot, 146 sweeps each. The earlier across-the-room
    # captures (#2-#5) are NOT used: at that distance the box sat under the
    # -64 dBm floor, which is exactly why they showed nothing and why I wrongly
    # cleared it. Distance, not innocence.
    if all(n in m for n in (6, 7, 8)):
        trials.append(("FancyLEDs box (Tuya)", [(m[7], m[6]), (m[7], m[8])]))

    # Xbox - CLOSE RANGE, FancyLEDs left ON for both so only the Xbox changes.
    #   #9 = Xbox OFF    #8 = Xbox ON
    # This turned out to be the DOMINANT emitter in 2414-2430: roughly 30 points
    # against the FancyLEDs box's ~13-20 in the very same band. Two transmitters
    # stacked on one block of spectrum.
    # Two independent ON trials. Note the magnitudes differ ~5x (+29.8 vs +6.0)
    # at the same frequencies: the Xbox's output depends on what it is doing.
    # Busy it takes ~30 points, idle ~6. That variability is itself the finding -
    # it matches interference that comes and goes rather than sitting constant.
    if all(n in m for n in (8, 9, 10)):
        trials.append(("Xbox (console / controller radio)",
                       [(m[9], m[8]), (m[9], m[10])]))
    elif all(n in m for n in (8, 9)):
        trials.append(("Xbox (console / controller radio)", [(m[9], m[8])]))

    # Buds + transmitter - scanner moved CLOSE to them.
    #   #12 = both OFF      #11 = both ON
    # Caveat worth knowing: 2415-2429 also moved between these two captures, but
    # DOWNWARD - turning the buds on cannot make the band quieter. That is the
    # Xbox/FancyLEDs changing activity between runs, and it contaminates that
    # part of the comparison. The 2459-2466 rise sits outside their band, so it
    # is the part that can be trusted.
    if all(n in m for n in (11, 12)):
        trials.append(("Galaxy Buds 3 Pro + transmitter", [(m[12], m[11])]))
    elif all(n in m for n in (1, 2)):
        trials.append(("Galaxy Buds 3 Pro + transmitter (across the room)",
                       [(m[2], m[1])]))

    # Whatever survives with BOTH the Xbox and the FancyLEDs accounted for.
    # Capture #9 is Xbox OFF (FancyLEDs still on), so anything still loud there
    # is a third source nobody has switched yet.
    suspects = []
    if 9 in m:
        band = list(range(17, 29))                 # 2417-2428 MHz
        level = sum(m[9][c] for c in band) / len(band)
        if level >= 10:
            suspects.append({
                "lo": 17, "hi": 28, "level": level,
                "note": ("Still running at this level with the Xbox powered OFF and "
                         "only the FancyLEDs box left on. So a THIRD transmitter "
                         "shares this block. It is wideband and continuous - the "
                         "shape that starves Bluetooth of somewhere to hop."),
                "candidates": ["Android TV box (Wi-Fi)",
                               "The TV itself (Wi-Fi)",
                               "Your router / a neighbour's Wi-Fi on channel 1",
                               "soundcore Liberty 4 NC (paired to this PC, never tested)"],
            })

    return trials, suspects


def main():
    entries = {x["n"]: x for x in timeline.load()}
    if not entries:
        sys.exit("No timeline entries yet. Run a capture with --label first.")
    m = {n: entries[n]["mean"] for n in entries}

    trials, suspects = build_trials(m)
    atts = [report.attribute(name, runs) for name, runs in trials]

    for a in atts:
        bands = [(report.MHZ(b["lo"]), report.MHZ(b["hi"]), round(b["avg"], 1))
                 for b in a["bands"]]
        drop = [(report.MHZ(d["lo"]), report.MHZ(d["hi"]))
                for d in a.get("dropped", [])]
        print(f"{a['name']}")
        print(f"   verdict    : {a['verdict']}  ({a['runs']} trial(s))")
        print(f"   confidence : {a.get('confidence', 0)}%")
        print(f"   bands      : {bands or '-'}")
        if drop:
            print(f"   dropped    : {drop}  (seen once, not on repeat)")
    for s in suspects:
        print(f"UNCLAIMED {report.MHZ(s['lo'])}-{report.MHZ(s['hi'])} MHz at {s['level']:.0f}%"
              f"  -> {len(s['candidates'])} suspects to test")

    latest = max(entries)
    html = report.build(m[latest],
                        {"when": entries[latest]["at"],
                         "sweeps": entries[latest]["sweeps"], "ms": 408},
                        atts, suspects)
    out = pathlib.Path(os.path.expanduser("~")) / "OneDrive" / "Desktop" / "rf24-report.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nWritten: {out}  ({len(html)//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
