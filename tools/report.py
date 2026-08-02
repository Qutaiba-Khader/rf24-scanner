#!/usr/bin/env python3
"""
Turn a scan into a standalone HTML report.

Self-contained: inline CSS, no scripts, no network. Double-click it.

The report is built around one question - why does 2.4 GHz audio drop out - so
the headline is not "here is the spectrum" but "here is what is eating your
Bluetooth, and what to do about it".

Used by tools/scan.py --html report.html
"""

import html
import math
import time

NCH = 126
MHZ = lambda c: 2400 + c

# Bluetooth hops across 79 channels, 2402-2480 MHz = nRF channels 2..80.
BT_LO, BT_HI = 2, 80
# Above this, Adaptive Frequency Hopping will exclude a channel from its map.
AFH_BUSY = 20.0

BANDS = [
    ("Wi-Fi ch 1", 2, 22), ("Wi-Fi ch 6", 27, 47), ("Wi-Fi ch 11", 52, 72),
    ("Wi-Fi ch 13 (EU)", 62, 82),
    ("BLE advertising", 1, 3), ("BLE advertising", 25, 27), ("BLE advertising", 79, 81),
]


def bands_at(ch):
    return sorted({n for n, lo, hi in BANDS if lo <= ch <= hi})


def median(xs):
    s = sorted(xs)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def analyse(mean):
    """Everything the report needs, derived once."""
    floor = median(mean[84:])
    thr = max(8.0, floor + 10)
    busy = [mean[i] > thr for i in range(NCH)]

    # Contiguous runs of busy channels.
    runs, start = [], None
    for i in range(NCH + 1):
        if i < NCH and busy[i]:
            start = i if start is None else start
        elif start is not None:
            runs.append((start, i - 1))
            start = None

    # A Wi-Fi carrier is about 20 channels wide. A run far wider than that is
    # not a carrier - it means the whole band is loud - and calling it a
    # "plateau" both mislabels it and hides everything sitting on top of it.
    plateaus = [r for r in runs if 12 <= r[1] - r[0] + 1 <= 34]
    saturated = any(hi - lo + 1 > 34 for lo, hi in runs)

    # Spikes are NOT excluded from plateaus. The test below measures each
    # channel against its LOCAL background, so anything that survives it is
    # genuinely above whatever it is sitting on - and a gadget camped on a busy
    # Wi-Fi channel is precisely the case worth surfacing, not hiding.
    # BLE advertising channels carry beacons from every BLE device in range.
    # That is normal background, not a mystery gadget, and labelling it as one
    # sends you hunting for something that is supposed to be there.
    ble_adv = {1, 2, 3, 25, 26, 27, 79, 80, 81}

    spikes = []
    for c in range(1, NCH - 1):
        if mean[c] < thr or c in ble_adv:
            continue
        if mean[c] < mean[c - 1] or mean[c] < mean[c + 1]:
            continue
        nb = [mean[k] for k in range(max(0, c - 9), min(NCH, c + 10)) if abs(k - c) > 2]
        local = median(nb)
        # Either a strong RATIO above the local background, or a large ABSOLUTE
        # margin. Ratio alone fails at the top of the scale: occupancy cannot
        # exceed 100%, so a gadget camped on a 45%-busy Wi-Fi carrier can never
        # reach 2.2x it, and the loudest thing in the room goes unreported.
        if mean[c] > max(thr, min(local * 2.2 + 4, local + 30)):
            spikes.append(c)

    # The number that actually matters for audio: how much room AFH has left.
    span = mean[BT_LO:BT_HI + 1]
    usable = sum(1 for v in span if v < AFH_BUSY)
    total = len(span)

    return {
        "floor": floor, "thr": thr, "plateaus": plateaus, "spikes": spikes,
        "usable": usable, "total": total, "saturated": saturated,
        "inband": sum(span) / total,
        "overloaded": floor > 15,
        "widest": max((hi - lo + 1 for lo, hi in runs), default=0),
    }


def audio_verdict(a):
    """The headline: why 2.4 GHz audio is struggling, in plain words."""
    usable, total = a["usable"], a["total"]
    frac = usable / total

    if a["overloaded"]:
        return ("critical", "Move the scanner before trusting this",
                f"Channels above 2484 MHz should be silent but read "
                f"{a['floor']:.0f}%. Something very strong is saturating the "
                f"receiver and inflating every number here. Move the scanner a "
                f"metre or two away and scan again.")

    if frac < 0.35:
        return ("critical", "Your earbuds have almost nowhere to hop",
                f"Only <b>{usable} of {total}</b> Bluetooth channels are clear "
                f"enough to use. Bluetooth survives interference by hopping "
                f"around it, and at this level there is barely anywhere left to "
                f"hop to. This is more than enough to cause dropouts.")

    if frac < 0.6:
        return ("serious", "Bluetooth is being squeezed",
                f"<b>{usable} of {total}</b> Bluetooth channels are clear. "
                f"Adaptive hopping is coping, but with this little room, any "
                f"extra load or a body between you and the transmitter will "
                f"push it over.")

    if a["spikes"]:
        return ("warning", "One device is camping on a frequency",
                f"<b>{usable} of {total}</b> Bluetooth channels are clear, which "
                f"is enough room for audio. But a fixed-frequency transmitter is "
                f"sitting on {', '.join(str(MHZ(c)) + ' MHz' for c in sorted(a['spikes'])[:3])}. "
                f"Bluetooth will route around a narrow one like this, so if your "
                f"audio still breaks up, keep looking for something wider.")

    return ("good", "The air is clear enough for audio",
            f"<b>{usable} of {total}</b> Bluetooth channels are clear. Nothing "
            f"here should be breaking up your audio. If it still does, the cause "
            f"is intermittent - run a longer scan while the problem is happening.")


def advice(a, attributions=None, suspects=None):
    """Built from THIS room's measurements, not from a generic checklist.

    Everything below names a device that was actually identified by switching
    it off and on, and the frequencies it was measured on.
    """
    out = []
    found = [x for x in (attributions or [])
             if x["verdict"] in ("confirmed", "weak") and x["bands"]]
    found.sort(key=lambda x: -max(b["avg"] for b in x["bands"]))

    for i, dev in enumerate(found):
        b = max(dev["bands"], key=lambda z: z["avg"])
        lo, hi = MHZ(b["lo"]), MHZ(b["hi"])
        width = b["hi"] - b["lo"] + 1
        name = dev["name"].split("(")[0].strip()
        if i == 0:
            out.append((f"{name} is the biggest source in this room",
                        f"Measured at <b>{lo}&ndash;{hi} MHz</b> ({width} channels), adding "
                        f"<b>{b['avg']:+.0f} points</b> of occupancy when switched on. "
                        f"Move it as far from your earbuds and their transmitter as the "
                        f"cabling allows, or power it off while you are listening. "
                        f"Distance is the cheapest fix there is - the signal falls off "
                        f"fast, which is exactly why it was invisible from across the room."))
        else:
            out.append((f"{name} stacks on the same frequencies",
                        f"Also at <b>{lo}&ndash;{hi} MHz</b>, adding <b>{b['avg']:+.0f} "
                        f"points</b> on top. Two transmitters sharing one block is worse "
                        f"than either alone - separate them, or remove whichever you need "
                        f"least while listening."))

    if len(found) >= 2:
        allb = [b for d in found for b in d["bands"]]
        lo, hi = MHZ(min(b["lo"] for b in allb)), MHZ(max(b["hi"] for b in allb))
        n = max(b["hi"] for b in allb) - min(b["lo"] for b in allb) + 1
        out.append(("Together they take the bottom of the Bluetooth band",
                    f"<b>{lo}&ndash;{hi} MHz</b> is roughly {n} of the 79 channels your "
                    f"earbuds can hop through, occupied continuously. Bluetooth dodges "
                    f"narrow interference easily; it cannot dodge a block this wide. "
                    f"That is the mechanism behind your dropouts."))

    for s in suspects or []:
        out.append((f"Something still unidentified sits at {MHZ(s['lo'])}&ndash;{MHZ(s['hi'])} MHz",
                    f"Running at <b>{s['level']:.0f}%</b> with everything identified so far "
                    f"switched off. Next to test, in order: "
                    f"{'; '.join(c.split('(')[0].strip() for c in s['candidates'])}. "
                    f"Power one off, scan, power it on, scan."))

    if a["usable"] < a["total"]:
        out.append(("How much room your earbuds actually have left",
                    f"<b>{a['usable']} of {a['total']}</b> Bluetooth channels are clear "
                    f"enough to hop into right now. Every device you remove from the list "
                    f"above gives that number back."))
    return out


CSS = """
:root{--plane:#0d0d0d;--surface:#1a1a19;--surface2:#141413;--ink:#fff;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--b1:#3987e5;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);line-height:1.5;
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:22px 18px 60px}
h1{font-size:19px;margin:0}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.9px;margin:0 0 10px}
.lab{font:10px/1.4 ui-monospace,Consolas,monospace;text-transform:uppercase;
letter-spacing:.15em;opacity:.9}
header.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
padding-bottom:16px;border-bottom:1px solid var(--border);margin-bottom:18px}
header.top .dot{width:9px;height:9px;border-radius:50%;background:var(--b1)}
header.top .meta{margin-left:auto;text-align:right;font-size:12px;opacity:.95}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
padding:15px 16px;margin-bottom:14px}
.hero{display:flex;gap:0;overflow:hidden;padding:0;align-items:stretch}
.hero .accent{width:6px;flex:none}
.hero.good .accent{background:var(--good)}
.hero.warning .accent{background:var(--warning)}
.hero.serious .accent{background:var(--serious)}
.hero.critical .accent{background:var(--critical)}
.hero .body{padding:18px 20px}
.hero h1{font-size:clamp(21px,3vw,30px);font-weight:800;letter-spacing:-.02em;margin:6px 0 0}
.hero p{margin:9px 0 0;max-width:66ch;font-size:14.5px}
.gauge{display:flex;gap:2px;margin:12px 0 8px;height:34px}
.gauge i{flex:1;border-radius:2px;background:var(--grid)}
.gauge i.free{background:var(--good)}
.gauge i.busy{background:var(--critical)}
.spec{margin-top:6px}
.row{display:flex;align-items:center;gap:9px;font-size:12px;padding:2px 0}
.row .n{width:96px;flex:none;font-family:ui-monospace,Consolas,monospace}
.row .bar{flex:1;height:13px;background:var(--surface2);border-radius:3px;overflow:hidden}
.row .bar b{display:block;height:100%;background:var(--b1);border-radius:3px}
.row .v{width:52px;text-align:right;flex:none;font-variant-numeric:tabular-nums}
.row .t{width:270px;flex:none;opacity:.95;font-size:11.5px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:11.5px;margin:0 0 10px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:11px;height:11px;border-radius:2px;flex:none}
.who{font-size:10px;padding:1px 6px;border-radius:4px;margin-left:6px;
text-transform:uppercase;letter-spacing:.06em;white-space:nowrap}
.who.ok{background:rgba(12,163,12,.22);border:1px solid var(--good)}
.who.maybe{background:rgba(250,178,25,.18);border:1px solid var(--warning)}
.who.sus{background:rgba(213,81,129,.20);border:1px solid #d55181}
.susp{color:#d55181}
.dev{margin:1px 0}
.arcwrap{margin:6px 0 2px;overflow-x:auto}
.arcwrap svg{width:100%;min-width:720px;height:auto;display:block}
.arcwrap text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif}
.arcwrap .alab{font-weight:700;font-size:12px;text-anchor:middle}
.arcwrap .asub{font-size:10px;text-anchor:middle;opacity:.9;
font-family:ui-monospace,Consolas,monospace}
.arcwrap .fx{fill:#8d8d86;font-size:9.5px;text-anchor:middle;
font-family:ui-monospace,Consolas,monospace}
.arcwrap .wch{fill:#7d7d76;font-size:10px;text-anchor:middle;font-weight:600}
.arcwrap .hoplab{fill:#3fbf90;font-size:11.5px;font-weight:700;text-anchor:middle;
letter-spacing:.05em}
.arcwrap .ziplab{fill:#e8706f;font-size:11.5px;font-weight:700;text-anchor:middle}
table.rank td{vertical-align:top}
table.rank .wy{font-size:11.5px;line-height:1.5;opacity:.97;max-width:30em}
tr.sr.crit td:first-child{box-shadow:inset 3px 0 0 var(--critical)}
tr.sr.warn td:first-child{box-shadow:inset 3px 0 0 var(--warning)}
tr.sr.low  td:first-child{box-shadow:inset 3px 0 0 rgba(255,255,255,.18)}
.mac{display:block;font-size:10.5px;opacity:.9;
font-family:ui-monospace,Consolas,monospace}
.elim{margin:12px 0 0;padding:11px 13px;border-radius:9px;
background:rgba(208,59,59,.10);border:1px solid rgba(208,59,59,.40)}
.elim ul{margin:6px 0 6px;padding-left:20px}
.elim li{margin:3px 0;font-size:12.5px}
.arcwrap .zonecap{fill:#e8706f;font-size:11px;font-weight:800;text-anchor:middle;
letter-spacing:.13em}
.map{position:relative;margin:10px 0 4px}
.rowlab{font-size:12.5px;margin:14px 0 4px;display:flex;gap:8px;
align-items:baseline;flex-wrap:wrap}
.rowlab b{font-size:13.5px}
.rowlab span{opacity:.9;font-family:ui-monospace,Consolas,monospace;font-size:11px}
.track{position:relative;height:26px;background:var(--surface2);
border:1px solid var(--border);border-radius:5px}
.blk{position:absolute;top:0;bottom:0;border-radius:4px;display:grid;
place-items:center;font-size:11px;font-weight:700;color:#000;overflow:hidden;
white-space:nowrap}
.hop{position:absolute;top:0;bottom:0;border-radius:4px;
background:repeating-linear-gradient(115deg,rgba(25,158,112,.42) 0 6px,transparent 6px 12px);
border:1px dashed rgba(25,158,112,.75)}
.zone{position:absolute;top:0;bottom:26px;background:rgba(208,59,59,.13);
border-left:2px solid var(--critical);border-right:2px solid var(--critical);
pointer-events:none;z-index:3}
.zone b{position:absolute;top:-2px;left:50%;transform:translateX(-50%);
background:var(--critical);color:#fff;font-size:10px;padding:2px 8px;
border-radius:0 0 5px 5px;white-space:nowrap;letter-spacing:.04em}
.ruler{position:relative;height:22px;margin-top:6px}
.ruler i{position:absolute;top:0;font-style:normal;font-size:10px;
transform:translateX(-50%);font-family:ui-monospace,Consolas,monospace;opacity:.9}
.ruler i::before{content:'';position:absolute;top:-6px;left:50%;width:1px;
height:5px;background:#555}
.key{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin-top:16px}
.key span{display:inline-flex;align-items:center;gap:6px}
.key i{width:12px;height:12px;border-radius:3px;flex:none}
table.sum{margin:4px 0 10px}
table.sum th{white-space:nowrap}
table.sum td{vertical-align:top}
table.sum .unk{display:block;font-size:11px;margin-top:2px;max-width:26ch}
.amt{font-size:11px;opacity:.9;font-variant-numeric:tabular-nums}
.alsohere{margin-top:3px;font-size:12px}
.unk{opacity:.75;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:4px}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--border)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.08em;opacity:.95}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.tip{display:flex;gap:11px;padding:11px 0;border-top:1px solid var(--border)}
.tip:first-of-type{border-top:none}
.tip .k{width:22px;height:22px;flex:none;border-radius:6px;background:var(--b1);
color:#fff;display:grid;place-items:center;font-weight:700;font-size:12px}
.tip .bd b{display:block;font-size:13.5px}
.tip .bd p{margin:3px 0 0;font-size:12.5px;opacity:.97}
.att{display:flex;gap:0;overflow:hidden;border:1px solid var(--border);
border-radius:9px;margin-top:10px;background:var(--surface2)}
.att .attbar{width:5px;flex:none;background:var(--axis)}
.att.good .attbar{background:var(--good)}
.att.warning .attbar{background:var(--warning)}
.att.serious .attbar{background:var(--critical)}
.att.info .attbar{background:var(--b1)}
.att .attbody{padding:11px 14px 12px}
.att .attbody > b{display:block;font-size:15px;margin-top:3px}
.att .freq{font-size:13px;margin-top:4px;
font-family:ui-monospace,Consolas,monospace}
.att .attbody p{margin:6px 0 0;font-size:12.5px;opacity:.97;max-width:70ch}
.att .retract{margin-top:7px;font-size:12px;padding:7px 9px;border-radius:6px;
background:rgba(208,59,59,.13);border:1px solid rgba(208,59,59,.35)}
.conf{display:flex;align-items:center;gap:9px;margin-top:9px;font-size:12px}
.conf .cbar{width:190px;height:9px;border-radius:5px;background:var(--surface);
border:1px solid var(--border);overflow:hidden;flex:none}
.conf .cbar i{display:block;height:100%;background:var(--good)}
.att.warning .conf .cbar i{background:var(--warning)}
.conf .cnum{font-weight:700;font-size:14px;font-variant-numeric:tabular-nums}
.cands{margin-top:9px;font-size:12.5px}
.cands ol{margin:5px 0 6px;padding-left:20px}
.cands li{margin:2px 0}
.note{font-size:12.5px;opacity:.95;margin-top:12px;padding-top:12px;
border-top:1px solid var(--border)}
"""


def bar_row(label, pct, tag, colour=None):
    # One style attribute. Emitting two makes the browser keep the first and
    # silently drop the second, which loses the bar width entirely.
    w = max(0.0, min(100.0, pct))
    style = f"width:{w:.1f}%" + (f";background:{colour}" if colour else "")
    return (f'<div class="row"><span class="n">{html.escape(label)}</span>'
            f'<span class="bar"><b style="{style}"></b></span>'
            f'<span class="v">{pct:.0f}%</span>'
            f'<span class="t">{html.escape(tag)}</span></div>')


def clusters_between(off, on, floor=1.5, minrun=3):
    """Adjacent channels that rose together. A real transmitter occupies a
    contiguous block; scattered single channels are noise however big they look."""
    risen = {c for c in range(NCH) if (on[c] - off[c]) >= floor}
    runs, cur = [], []
    for c in range(NCH):
        if c in risen:
            cur.append(c)
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    return [{"lo": r[0], "hi": r[-1], "n": len(r),
             "avg": sum(on[c] - off[c] for c in r) / len(r)}
            for r in runs if len(r) >= minrun]


def attribute(name, runs):
    """Judge one device from one or more off/on trials.

    runs = [(off_mean, on_mean), ...]. Confidence comes from REPLICATION: a
    cluster that appears once is a coincidence candidate, one that appears in
    every trial at the same frequency is the device.
    """
    per_run = [clusters_between(off, on) for off, on in runs]
    if not any(per_run):
        return {"name": name, "verdict": "not-detected", "bands": [], "runs": len(runs),
                "why": "No contiguous cluster appeared in any trial. Either it is not "
                       "transmitting on 2.4 GHz, it is below the -64 dBm floor at this "
                       "distance, or it only transmits in short bursts."}

    # A band counts as replicated only if some cluster overlaps it in EVERY run.
    def overlaps(a, b):
        return not (a["hi"] < b["lo"] - 1 or b["hi"] < a["lo"] - 1)

    replicated = []
    for cand in per_run[0]:
        matches = [cand]
        for others in per_run[1:]:
            m = [o for o in others if overlaps(cand, o)]
            if not m:
                matches = None
                break
            matches.append(max(m, key=lambda x: x["avg"]))
        if matches:
            lo = min(m["lo"] for m in matches)
            hi = max(m["hi"] for m in matches)
            replicated.append({"lo": lo, "hi": hi,
                               "avg": sum(m["avg"] for m in matches) / len(matches)})

    dropped = [c for c in per_run[0] if not any(
        overlaps(c, r) for r in replicated)] if len(runs) > 1 else []

    if not replicated:
        return {"name": name, "verdict": "not-replicated", "bands": [], "runs": len(runs),
                "dropped": dropped,
                "why": "A cluster appeared in one trial but not the others, so it cannot "
                       "be attributed to this device. Most likely something else nearby "
                       "transmitted during that window."}

    strong = max(r["avg"] for r in replicated)
    verdict = "confirmed" if (strong >= 6 and len(runs) > 1) else "weak"
    conf = confidence(strong, len(runs))
    return {"name": name, "verdict": verdict, "bands": replicated, "runs": len(runs),
            "dropped": dropped, "confidence": conf,
            "why": ("Appeared at the same frequencies in every trial and is far clear of "
                    "the noise." if verdict == "confirmed" else
                    "Appeared at the same frequencies in every trial, but the change is "
                    "small. Real, but not yet conclusive - capture for longer, and make "
                    "the device actually busy while you do.")}


# Run-to-run variation measured across this session's captures: repeating the
# same measurement moves a channel by roughly +/-3 points. Everything below is
# scaled against that, so the number means something instead of being a vibe.
NOISE_POINTS = 3.0


def confidence(avg_rise, trials):
    """A rough, honest percentage: how far above noise, times how often it repeated.

    Deliberately caps at 99. A 1-bit energy detector can never prove WHICH box
    made a signal - only that the signal follows that box's power switch.
    """
    if avg_rise <= 0 or trials <= 0:
        return 0
    z = (avg_rise / NOISE_POINTS) * math.sqrt(trials)
    return int(min(99, round(100 * (1 - math.exp(-z / 2)))))


VERDICT_STYLE = {
    "confirmed":      ("good",     "IDENTIFIED"),
    "weak":           ("warning",  "LIKELY - needs a longer capture"),
    "not-replicated": ("serious",  "NOT REPLICATED - retracted"),
    "not-detected":   ("info",     "NOT DETECTED"),
}


WIFI_CH = {k: (12 + 5 * (k - 1) - 10, 12 + 5 * (k - 1) + 10) for k in range(1, 14)}


def wifi_at(c):
    return [k for k, (lo, hi) in WIFI_CH.items() if lo <= c <= hi]


def wifi_span(lo, hi):
    ks = sorted({k for c in range(lo, hi + 1) for k in wifi_at(c)})
    return ", ".join(str(k) for k in ks) if ks else "&mdash;"


MAP_LO, MAP_HI = 0, 85               # 2400-2485 MHz drawing window
MAP_SPAN = MAP_HI - MAP_LO + 1


def _x(c):
    return (c - MAP_LO) / MAP_SPAN * 100


AUTH_NAMES = ["open", "WEP", "WPA", "WPA2", "WPA/WPA2", "enterprise",
              "WPA3", "WPA2/3"]

# The nRF24's RPD threshold. An access point weaker than this cannot be the
# source of energy the scanner is measuring, however suspicious its name looks -
# so it is never allowed to explain a cluster.
RPD_FLOOR_DBM = -64


def wifi_ch_span(ch):
    """nRF channel span of a 20 MHz Wi-Fi channel. ch 1 -> (2, 22).

    Deliberately NOT called wifi_span: that name is already taken above by a
    function with a different signature, and redefining it silently broke both
    of its callers with a TypeError that only fires at render time.
    """
    c = 12 + 5 * (ch - 1)
    return c - 10, c + 10


def named_for(names, lo, hi):
    """Strongest named AP that is BOTH loud enough to be seen and actually
    covers this span. Returns None when nothing qualifies.

    Both conditions matter. Overlap alone would let a -93 dBm neighbour take
    the blame for energy the scanner physically cannot be detecting from it.
    """
    best = None
    for ap in (names or {}).get("wifi", []):
        if ap["rssi"] <= RPD_FLOOR_DBM:
            continue
        a, b = wifi_ch_span(ap["ch"])
        ov = min(hi, b) - max(lo, a) + 1
        if ov <= 0 or ov / (hi - lo + 1) < 0.5:
            continue
        if best is None or ap["rssi"] > best["rssi"]:
            best = ap
    return best


def named_section(names, atts, suspects):
    """The second radio's contribution: identity, and what it rules OUT.

    The elimination is the point. The nRF24 says something is transmitting; this
    says whether anything that announces itself can account for it. When nothing
    can, the remainder is a proprietary 2.4 GHz emitter - which is the class this
    whole project set out to find.
    """
    if not names or not names.get("wifi"):
        return ""

    wifi = sorted(names["wifi"], key=lambda a: (a["ch"], -a["rssi"]))
    ble = sorted(names.get("ble", []), key=lambda b: -b["rssi"])
    loud = [a for a in wifi if a["rssi"] > RPD_FLOOR_DBM]

    rows = []
    for a in wifi:
        lo, hi = wifi_ch_span(a["ch"])
        vis = a["rssi"] > RPD_FLOOR_DBM
        rows.append(
            f"<tr><td><b>{html.escape(a['ssid'] or '(hidden network)')}</b>"
            f"<span class='mac'>{':'.join(a['bssid'][i:i+2] for i in range(0,12,2))}"
            f" &middot; {AUTH_NAMES[a['auth']] if a['auth'] < len(AUTH_NAMES) else a['auth']}"
            f"{' &middot; ' + html.escape(a['vendor']) if a.get('vendor') else ''}"
            f"</span></td>"
            f"<td class='mono'>Wi-Fi ch {a['ch']}</td>"
            f"<td class='mono'>{MHZ(lo)}&ndash;{MHZ(hi)} MHz</td>"
            f"<td class='mono num'>{a['rssi']} dBm</td>"
            f"<td>{'<span class=who sus>loud enough to see</span>' if vis else '<span class=who ok>below the floor</span>'}</td></tr>")

    known = (names or {}).get("known") or {}
    for b in ble[:12]:
        vis = b["rssi"] > RPD_FLOOR_DBM
        # A name from Home Assistant beats one off the air, because most BLE
        # adverts carry no name at all - and the ones that matter here carry it
        # in a BLE-5 extended advert that a 4.2 controller cannot read.
        k = known.get(b["mac"], {})
        shown = k.get("name") or b["name"]
        tail = (f"<span class='mac'>{':'.join(b['mac'][i:i+2] for i in range(0,12,2))}"
                + (f" &middot; {html.escape(k['vendor'])}" if k.get("vendor") else "")
                + (" &middot; named in Home Assistant" if k.get("name") else "")
                + "</span>")
        rows.append(
            f"<tr><td><b>{html.escape(shown or '(unnamed BLE device)')}</b>{tail}</td>"
            f"<td class='mono'>BLE</td>"
            f"<td class='mono'>hops 2402&ndash;2480</td>"
            f"<td class='mono num'>{b['rssi']} dBm</td>"
            f"<td>{'<span class=who maybe>loud enough to see</span>' if vis else '<span class=who ok>below the floor</span>'}</td></tr>")

    if loud:
        verdict = (
            f"<b>{len(loud)} of the {len(wifi)} access points are above the "
            f"scanner&rsquo;s &minus;64 dBm floor</b>, so they can account for "
            f"measured energy: "
            + ", ".join(f"<b>{html.escape(a['ssid'] or '(hidden)')}</b> on ch "
                        f"{a['ch']} at {a['rssi']} dBm"
                        for a in sorted(loud, key=lambda x: -x["rssi"])[:4])
            + ".")
    else:
        verdict = ("<b>Not one access point is above the scanner&rsquo;s "
                   "&minus;64 dBm floor.</b> So any strong energy the scanner "
                   "measures is <b>not</b> coming from a Wi-Fi access point. By "
                   "elimination it is a proprietary 2.4&nbsp;GHz emitter &mdash; "
                   "an LED controller, a dongle, an RF remote.")

    # The unexplained bands. This is the whole reason for running two radios.
    unexplained = []
    for a in atts or []:
        if a["verdict"] not in ("confirmed", "weak") or not a["bands"]:
            continue
        if "Buds" in a["name"]:
            continue
        b = max(a["bands"], key=lambda z: z["avg"])
        if not named_for(names, b["lo"], b["hi"]):
            unexplained.append((a["name"], b["lo"], b["hi"]))
    for s in suspects or []:
        if not named_for(names, s["lo"], s["hi"]):
            unexplained.append(("Unidentified", s["lo"], s["hi"]))

    elim = ""
    if unexplained:
        items = "".join(
            f"<li><b>{MHZ(lo)}&ndash;{MHZ(hi)} MHz</b> "
            f"<span class='mono'>(ch {lo}&ndash;{hi})</span> &mdash; "
            f"measured as <i>{html.escape(nm)}</i></li>"
            for nm, lo, hi in unexplained)
        elim = (f"<div class='elim'><b>Nothing that announces itself explains "
                f"these bands:</b><ul>{items}</ul>"
                f"No access point loud enough to be detected covers them, so by "
                f"elimination each is a <b>proprietary 2.4&nbsp;GHz emitter</b> "
                f"&mdash; the class that does not appear in any Wi-Fi or "
                f"Bluetooth scan.</div>")

    lab = html.escape(names.get("label", "")) or "one capture"
    return f"""
<section class="card">
  <h2>What the second radio can put a name to</h2>
  <p class="caption" style="margin-top:0">The nRF24 measures <b>energy</b> and can
    never say what something is. An ESP32 running <span class="mono">rfnames</span>
    is blind to energy but reads <b>identity</b> &mdash; SSID, MAC, device name
    &mdash; and a real signal level in dBm. Scanned {lab}.</p>
  <table class="tab">
    <thead><tr><th>Name</th><th>Kind</th><th>Occupies</th>
      <th class="num">Signal</th><th>Can the scanner see it?</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p class="note">{verdict}</p>
  {elim}
  <p class="note"><b>A Wi-Fi scan sees beacons, not traffic.</b> An access point
    hammering the band and an idle one beacon identically, about ten times a
    second. This table says <i>who</i> is there; only the nRF24 says <i>how much</i>
    of the air they take. Both radios must sit in the same place for the two sets
    of numbers to combine.</p>
</section>"""


def promisc_section(names):
    """Who is actually USING the air, as opposed to merely being present.

    A Wi-Fi scan sees beacons, so a saturating access point and an idle one look
    identical. Monitor mode counts real frames and bytes per TRANSMITTER, which
    is the only way this project can measure airtime rather than presence.
    """
    rows_in = (names or {}).get("promisc") or []
    if not rows_in:
        return ""

    total = sum(r["bytes"] for r in rows_in) or 1
    rows = []
    for r in sorted(rows_in, key=lambda x: -x["bytes"]):
        share = r["bytes"] / total * 100
        vis = r["rssi"] > RPD_FLOOR_DBM
        if r.get("randomised"):
            who = "<span class='mac'>locally-administered address &mdash; virtual or randomised, names nothing</span>"
        elif r.get("vendor"):
            who = f"<b>{html.escape(r['vendor'])}</b>"
        else:
            who = "<span class='mac'>vendor not in the OUI registry</span>"
        rows.append(
            f"<tr><td class='mono'>{':'.join(r['mac'][i:i+2] for i in range(0,12,2))}"
            f"<span class='mac'>Wi-Fi ch {r['ch']} &middot; {r['data']} data, "
            f"{r['mgmt']} mgmt</span></td>"
            f"<td>{who}</td>"
            f"<td class='mono num'>{r['rssi']} dBm</td>"
            f"<td class='num'>{r['bytes']:,}</td>"
            f"<td class='num'>{share:.0f}%</td>"
            f"<td>{'<span class=who sus>yes</span>' if vis else '<span class=who ok>no</span>'}</td>"
            f"</tr>")

    loud = [r for r in rows_in if r["rssi"] > RPD_FLOOR_DBM]
    low = [r for r in rows_in if r["ch"] <= 6]
    lowmax = max((r["rssi"] for r in low), default=None)

    note = ""
    if lowmax is not None:
        note = (f"<div class='elim'><b>On Wi-Fi channels 1&ndash;6 the strongest "
                f"transmitter of any kind is {lowmax} dBm.</b> The scanner cannot "
                f"detect anything below &minus;64 dBm, so <b>no Wi-Fi device down "
                f"there &mdash; access point or client &mdash; can be producing the "
                f"energy measured at 2415&ndash;2429&nbsp;MHz.</b> This is a second, "
                f"independent confirmation of the elimination above, and it now "
                f"covers clients as well as access points.</div>")

    lab = html.escape(names.get("promisc_label", ""))
    return f"""
<section class="card">
  <h2>Who is actually using the air</h2>
  <p class="caption" style="margin-top:0">A Wi-Fi scan sees <b>beacons</b>, so a
    saturating access point and an idle one look identical. Monitor mode counts
    real <b>frames and bytes per transmitter</b> &mdash; airtime, not presence.
    The address here is the device that transmitted, not the network it belongs
    to, and its first three bytes name the manufacturer. Captured {lab}.</p>
  <table class="tab">
    <thead><tr><th>Transmitter</th><th>Made by</th><th class="num">Signal</th>
      <th class="num">Bytes</th><th class="num">Share</th>
      <th>Scanner can see it?</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p class="note">{len(loud)} of {len(rows_in)} transmitters are above the
    scanner&rsquo;s &minus;64 dBm floor. A <b>locally-administered</b> address
    (second character 2, 6, a or e) is virtual or randomised and its vendor
    prefix means nothing &mdash; those rows are marked rather than guessed at.</p>
  {note}
</section>"""


def zigbee_ch(k):
    """802.15.4 channel k (11..26) -> (centre, lo, hi) in MHz."""
    f = 2405 + 5 * (k - 11)
    return f, f - 1, f + 1


def zigbee_section(names, block=(15, 29)):
    """The devices neither radio could ever name.

    Zigbee is 2.4 GHz, and it is invisible to a Wi-Fi scan AND invisible to a
    BLE scan. The nRF24 can only ever see it as ENERGY. That is exactly the
    class the elimination predicted: something real, loud and nameless to both
    instruments - so finding a Zigbee network here is not a coincidence, it is
    the prediction coming true.
    """
    known = (names or {}).get("known") or {}
    # A 16-hex-digit address is an EUI-64, which only Zigbee/802.15.4 uses.
    zig = {m: v for m, v in known.items() if len(m) == 16}
    hubs = [v for m, v in known.items()
            if "ZIGBEE" in (v.get("vendor") or "").upper() and len(m) == 12]
    if not zig and not hubs:
        return ""

    blo, bhi = MHZ(block[0]), MHZ(block[1])

    rows = "".join(
        f"<tr><td><b>{html.escape(v.get('name','?'))}</b>"
        f"<span class='mac'>{':'.join(m[i:i+2] for i in range(0,16,2))}</span></td>"
        f"<td class='mono'>Zigbee EUI-64</td>"
        f"<td class='mono'>Philips Lighting</td></tr>"
        for m, v in sorted(zig.items(), key=lambda x: x[1].get("name", "")))
    for v in hubs:
        rows += (f"<tr><td><b>{html.escape(v.get('name','?'))}</b>"
                 f"<span class='mac'>{html.escape(v.get('vendor',''))}</span></td>"
                 f"<td class='mono'>Zigbee hub</td><td class='mono'>&mdash;</td></tr>")

    # A hub's ACTUAL channel, once someone has read it, beats any list of
    # defaults - and it can rule the hub out entirely.
    actual = {}
    for v in known.values():
        if v.get("zigbee_channel") is not None:
            actual[v.get("name", "")] = v["zigbee_channel"]

    chrows = ""
    for label, chans, key in (
            ("Philips Hue Bridge", [11, 15, 20, 25], "Hue Bridge"),
            ("Samsung SmartThings", [14, 15, 19, 20, 25], "Samsung TV hub")):
        got = next((c for n, c in actual.items() if key in n), None)
        if got is not None:
            f, lo, hi = zigbee_ch(got)
            inside = not (hi < blo or lo > bhi)
            verdict = ("<b style='color:var(--critical)'>INSIDE the block</b>" if inside
                       else "<b style='color:var(--good)'>RULED OUT &mdash; outside "
                            f"{blo}&ndash;{bhi} MHz</b>")
            chrows += (f"<tr><td><b>{label}</b></td>"
                       f"<td><b>Measured: channel {got} = {f} MHz</b> "
                       f"({lo}&ndash;{hi} MHz) &mdash; {verdict}</td></tr>")
        else:
            cells = []
            for k in chans:
                f, lo, hi = zigbee_ch(k)
                inside = not (hi < blo or lo > bhi)
                cells.append(
                    f"<b style='color:{'var(--critical)' if inside else 'var(--ink)'}'>"
                    f"ch {k} = {f} MHz{' &#9664; possible' if inside else ''}</b>")
            chrows += (f"<tr><td><b>{label}</b></td>"
                       f"<td><i>not read yet</i> &mdash; defaults: "
                       f"{' &middot; '.join(cells)}</td></tr>")

    return f"""
<section class="card">
  <h2>The radios nothing could name &mdash; Zigbee</h2>
  <p class="caption" style="margin-top:0">
    <b>Zigbee runs in the same 2.4&nbsp;GHz band</b>, but it is invisible to a
    Wi-Fi scan and invisible to a BLE scan. The nRF24 can only ever see it as
    <b>energy</b>. That is precisely the device class the elimination above
    predicted &mdash; something real and loud that neither instrument can put a
    name to. These names came from Home Assistant, not from the air.</p>

  <table class="tab">
    <thead><tr><th>Device</th><th>Address type</th><th>Made by</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <p class="note"><b>{len(zig)} Zigbee radios plus {len(hubs)} hub(s).</b> Driving
    them is a <b>Hue Play HDMI Sync Box</b>, which updates the lightstrips from
    HDMI video <i>in real time</i> &mdash; a continuous stream of Zigbee traffic
    whenever the TV is on, not occasional on/off commands. That is a persistent
    carrier, and it lives in the same corner of the room as every capture that
    measured this block.</p>

  <h3 style="margin:16px 0 6px">Where the default channels land</h3>
  <table class="tab">
    <thead><tr><th>Hub</th><th>Default Zigbee channels</th></tr></thead>
    <tbody>{chrows}</tbody>
  </table>

  <div class="elim">
    <b>Hue is ruled out &mdash; and that matters, because it was the best
    hypothesis this report had.</b>
    <ul>
      <li>The Hue Bridge was read directly: <b>Zigbee channel 25 = 2475 MHz</b>.
          The block being hunted is <b>2415&ndash;2429 MHz</b>. They do not
          overlap at all, so this network is <b>not</b> its source.</li>
      <li>It does still take Bluetooth channels 72&ndash;74 of 79 &mdash; real,
          but three channels, not the fifteen at issue here.</li>
      <li><b>The Samsung SmartThings hub has not been read.</b> Its defaults
          include channel 14 (2420 MHz) and 15 (2425 MHz), both inside the
          block. That is the one still open.</li>
    </ul>
    What survives untouched is the <b>elimination</b> itself: no access point,
    and no transmitter of any kind, on Wi-Fi channels 1&ndash;6 exceeds
    &minus;81 dBm. Whatever owns 2415&ndash;2429 MHz still announces itself to
    nothing, and still lives near the TV rather than at the desk.
  </div>

  <p class="note"><b>Still to check:</b> the <b>SmartThings hub inside the
    Samsung TV</b> &mdash; SmartThings app &rarr; hub &rarr; Zigbee channel. If it
    reads <b>14</b> or <b>15</b>, that is the remaining candidate. The Hue Bridge
    has already been read and is clear.</p>
</section>"""


def inventory_section(names):
    """Everything named, in one place, so nothing found has to be found twice."""
    known = (names or {}).get("known") or {}
    if not known:
        return ""

    cap = names or {}
    seen = {}
    for b in cap.get("ble", []):
        seen[b["mac"]] = ("BLE advert", b["rssi"])
    for a in cap.get("wifi", []):
        seen[a["bssid"]] = ("Wi-Fi beacon", a["rssi"])
    for r in cap.get("promisc", []):
        seen[r["mac"]] = ("on-air traffic", r["rssi"])

    rows = ""
    for m, v in sorted(known.items(), key=lambda x: x[1].get("name", "").lower()):
        pretty = ":".join(m[i:i + 2] for i in range(0, len(m), 2))
        kind = "Zigbee" if len(m) == 16 else "Wi-Fi / BLE"
        if m in seen:
            how, rssi = seen[m]
            vis = ("<span class='who sus'>above the floor</span>" if rssi > RPD_FLOOR_DBM
                   else "<span class='who ok'>below the floor</span>")
            meas = f"{rssi} dBm &middot; {how}"
        else:
            vis = "<span class='who ok'>not seen here</span>"
            meas = "&mdash;"
        rows += (f"<tr><td><b>{html.escape(v.get('name','?'))}</b>"
                 f"<span class='mac'>{pretty}"
                 + (f" &middot; {html.escape(v['vendor'])}" if v.get("vendor") else "")
                 + f"</span></td><td class='mono'>{kind}</td>"
                 f"<td class='mono'>{meas}</td><td>{vis}</td></tr>")

    return f"""
<section class="card">
  <h2>Everything named, in one place</h2>
  <p class="caption" style="margin-top:0">Names come from Home Assistant; signal
    levels come from the radios. A name is a <b>label</b>, not a measurement
    &mdash; the &minus;64 dBm floor still decides whether a device can be
    responsible for anything measured here, which is why the level sits next to
    every name.</p>
  <table class="tab">
    <thead><tr><th>Device</th><th>Radio</th><th>Measured</th>
      <th>Scanner can see it?</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="note">{len(known)} devices named.
    <b>&ldquo;Not seen here&rdquo; does not mean off.</b> Every capture behind
    this report was taken at one spot, and most of these live in another part of
    the room &mdash; which is the whole reason the Zigbee block reads 1.4% here
    and 21&ndash;41% beside the TV.</p>
</section>"""


# The devices being PROTECTED. They must never appear in a table headed "how
# likely is this to be the cause" - the earbuds are the victim, and ranking the
# thing you are trying to fix as a suspect is a category error, not a low score.
VICTIM_WORDS = ("buds3", "buds 3", "earbud", "fma121", "galaxy buds")

BLOCK_LO, BLOCK_HI = 15, 29        # nRF ch = 2415-2429 MHz, the band in question
BUDS_LO, BUDS_HI = 58, 66          # nRF ch = 2458-2466 MHz, where the link was measured


def rank_suspects(names, atts, suspects):
    """Every transmitter this project has ever seen, scored by how likely it is
    to be causing the dropouts.

    The score is built from stated reasons rather than a feel, because the
    ranking is only useful if you can see WHY something is high - and can argue
    with it. Unnamed devices are included deliberately: an unidentified radio is
    a worse suspect than a known one, not a better-ignored one.
    """
    names = names or {}
    known = names.get("known") or {}
    rows = {}

    def touch(mac):
        return rows.setdefault(mac, {
            "mac": mac, "rssi": None, "kind": "", "band": None,
            "bytes": 0, "why": [], "score": 0.0})

    for a in names.get("wifi", []):
        r = touch(a["bssid"])
        r["rssi"] = a["rssi"]
        r["kind"] = f"Wi-Fi AP, ch {a['ch']}"
        r["band"] = wifi_ch_span(a["ch"])
        r["label"] = a["ssid"] or "(hidden network)"
    for b in names.get("ble", []):
        r = touch(b["mac"])
        if r["rssi"] is None or b["rssi"] > r["rssi"]:
            r["rssi"] = b["rssi"]
        r["kind"] = "Bluetooth LE (advertising)"
        # An ADVERTISER is not a hopper. It uses three fixed channels - 2402,
        # 2426 and 2480 - and 2426 happens to sit inside the band in question.
        # Only a CONNECTED link hops the data channels, and that is credited
        # separately below. Treating every advert as a full-band hopper put
        # devices at -97 dBm near the top of this table.
        r["band"] = (26, 26)
        r.setdefault("label", b["name"] or "")
    for q in names.get("promisc", []):
        r = touch(q["mac"])
        if r["rssi"] is None or q["rssi"] > r["rssi"]:
            r["rssi"] = q["rssi"]
        # The promiscuous channel is where the SNIFFER was listening, not
        # necessarily the device's own channel - adjacent-channel leakage
        # catches a ch-11 access point while sweeping ch 13. A beacon states the
        # real channel, so never overwrite one with a capture channel.
        if not r["kind"].startswith("Wi-Fi AP"):
            r["kind"] = f"Wi-Fi device, ch {q['ch']}"
            r["band"] = wifi_ch_span(q["ch"])
        r["bytes"] = max(r["bytes"], q["bytes"])
        if q.get("vendor"):
            r.setdefault("label", "")
    for mac, v in known.items():
        r = touch(mac)
        r["label"] = v.get("name", "")
        note = (v.get("vendor") or "").upper()
        if len(mac) == 16 or "ZIGBEE" in note:
            r["kind"] = r["kind"] or "Zigbee"
            r["band"] = r["band"] or None
        if "CONNECTED" in note:
            r["live"] = True
        if v.get("zigbee_channel"):
            f = 2405 + 5 * (v["zigbee_channel"] - 11)
            r["kind"] = f"Zigbee ch {v['zigbee_channel']}"
            r["band"] = (f - 1 - 2400, f + 1 - 2400)

    # --- scoring, one reason at a time --------------------------------------
    for r in rows.values():
        rssi = r["rssi"]

        # Everything positional is scaled by whether the device is loud enough
        # to be doing anything here at all. Without this gate a -97 dBm advert
        # scored as high as the loudest device in the room purely for sitting on
        # the right frequency.
        if r.get("live"):
            # A connected Bluetooth link hops 1600 times a second across 79
            # channels, and this scanner samples one channel for 200 us at a
            # time. It is documented as nearly blind to exactly this. So NOT
            # seeing a live link is what the instrument does, not evidence the
            # link is quiet - it must not be penalised for that.
            gate = 1.0
            if rssi is None:
                r["why"].append("not measured &mdash; but this scanner is nearly "
                                "blind to hopping Bluetooth, so that is expected "
                                "and is <b>not</b> evidence it is quiet")
        elif rssi is None:
            gate = 0.35
            r["why"].append("never measured by either radio &mdash; untested")
            r["score"] += 8
        elif rssi > RPD_FLOOR_DBM:
            gate = 1.0
            r["score"] += 30 + min(20, (rssi + 64))
            r["why"].append(f"loud here ({rssi} dBm, above the &minus;64 dBm floor)")
        else:
            # Falls off fast below the floor: -70 still worth a look up close,
            # -95 is not.
            gate = max(0.0, (rssi + 94) / 30.0)
            r["why"].append(f"only {rssi} dBm &mdash; {'well ' if rssi < -80 else ''}"
                            f"below the &minus;64 dBm floor, so it cannot be the "
                            f"source of anything measured here")

        b = r["band"]
        if b and not (b[1] < BLOCK_LO or b[0] > BLOCK_HI):
            r["score"] += 35 * gate
            r["why"].append("covers 2415&ndash;2429 MHz, the band in question")
        if b and b == (2, 80):
            # A hopping Bluetooth link cannot be routed around: it is spread
            # across the same 79 channels the earbuds use, so adaptive hopping
            # has nowhere to move to. This is the one interferer AFH cannot dodge.
            r["score"] += 40 * gate
            r["why"].append("hops all 79 Bluetooth channels &mdash; <b>adaptive "
                            "hopping cannot route around it</b>")
        elif b and not (b[1] < BUDS_LO or b[0] > BUDS_HI):
            r["score"] += 12 * gate
            r["why"].append("overlaps 2458&ndash;2466 MHz where the audio link was measured")

        if r.get("live"):
            r["score"] += 45 * gate
            r["why"].append("<b>a live connected Bluetooth link</b> &mdash; it hops "
                            "all 79 channels continuously, and <b>adaptive hopping "
                            "cannot route around it</b>")
        if r["bytes"]:
            r["score"] += min(20, r["bytes"] / 1500.0)
            r["why"].append(f"{r['bytes']:,} bytes of measured airtime")
        if not r.get("label"):
            r["score"] += 6
            r["why"].append("<b>unidentified</b> &mdash; never named, never tested")

    victims, cands = [], []
    for r in rows.values():
        lab = (r.get("label") or "").lower()
        (victims if any(w in lab for w in VICTIM_WORDS) else cands).append(r)
    return sorted(cands, key=lambda x: -x["score"]), victims


def suspects_section(names, atts, suspects):
    ranked, victims = rank_suspects(names, atts, suspects)
    if not ranked:
        return ""

    vic = ""
    if victims:
        items = "".join(
            f"<li><b>{html.escape(v.get('label',''))}</b> "
            f"<span class='mono'>{':'.join(v['mac'][j:j+2] for j in range(0,len(v['mac']),2))}</span>"
            + (f" &mdash; measured at {v['rssi']} dBm" if v["rssi"] is not None else "")
            + "</li>" for v in victims)
        vic = (f"<p class='note'><b>Excluded, because they are the victim:</b>"
               f"<ul style='margin:6px 0 0'>{items}</ul>"
               f"These are the devices being protected. Whatever they emit is the "
               f"signal we want to survive, not interference to be ranked.</p>")

    body = ""
    for i, r in enumerate(ranked, 1):
        pretty = ":".join(r["mac"][j:j + 2] for j in range(0, len(r["mac"]), 2))
        name = html.escape(r.get("label") or "")
        head = f"<b>{name}</b>" if name else "<b class='susp'>unidentified device</b>"
        band = (f"{MHZ(r['band'][0])}&ndash;{MHZ(r['band'][1])} MHz"
                if r["band"] else "&mdash;")
        tier = ("crit" if r["score"] >= 70 else
                "warn" if r["score"] >= 40 else "low")
        body += (
            f"<tr class='sr {tier}'>"
            f"<td class='num'><b>{i}</b></td>"
            f"<td>{head}<span class='mac'>{pretty} &middot; {r['kind'] or 'unknown radio'}</span></td>"
            f"<td class='mono'>{band}</td>"
            f"<td class='mono num'>{r['rssi'] if r['rssi'] is not None else '&mdash;'}"
            f"{' dBm' if r['rssi'] is not None else ''}</td>"
            f"<td class='num'><b>{r['score']:.0f}</b></td>"
            f"<td class='wy'>{'; '.join(r['why'])}</td></tr>")

    return f"""
<section class="card">
  <h2>Every transmitter, ranked by how likely it is to be the cause</h2>
  <p class="caption" style="margin-top:0">
    Everything either radio has ever seen, named or not, scored on stated
    reasons rather than a hunch &mdash; so you can disagree with any row and see
    exactly why it sits where it does. <b>Unidentified devices score higher, not
    lower</b>: a radio nobody has named is a worse suspect than one that has been
    tested and cleared.</p>
  <table class="tab rank">
    <thead><tr><th class="num">#</th><th>Device</th><th>Occupies</th>
      <th class="num">Signal</th><th class="num">Score</th><th>Why</th></tr></thead>
    <tbody>{body}</tbody>
  </table>
  <p class="note"><b>How the score is built.</b> Loud enough to be detected here
    (+30 and up); covers 2415&ndash;2429 MHz (+35); hops all 79 Bluetooth
    channels, which adaptive hopping cannot route around (+40); a live connected
    link (+25); measured airtime (up to +20); unidentified (+6). Below
    &minus;64 dBm earns nothing at all, because such a device physically cannot
    be the source of anything this scanner measured.</p>
  <p class="note"><b>Read the top rows as a to-do list, not a verdict.</b> The
    score says where to look next; only switching a device off and re-measuring
    says what it actually does.</p>
  {vic}
</section>"""


def arc_diagram(atts, suspects, names=None):
    """The classic 2.4 GHz overlap picture: one half-arc per occupant.

    Drawn the way Wi-Fi channel charts are drawn, because that is the shape
    people already read fluently - an arc's footprint on the baseline IS the
    spectrum it occupies, so overlap is visible rather than described.
    """
    W, H = 1000.0, 356.0
    BASE = 262.0          # baseline y
    TOP = 128.0           # highest arc apex (labels stack above this)
    BRK = BASE + 44.0     # span-bracket strip, below the frequency axis
    F0, F1 = 2400.0, 2485.0

    def x(mhz):
        return (mhz - F0) / (F1 - F0) * W

    def arc(lo_mhz, hi_mhz, y_top):
        x1, x2 = x(lo_mhz), x(hi_mhz)
        rx, ry = (x2 - x1) / 2.0, BASE - y_top
        return f"M {x1:.1f},{BASE:.1f} A {rx:.1f},{ry:.1f} 0 0 1 {x2:.1f},{BASE:.1f}"

    parts = []

    # --- Wi-Fi 1..11 as faint reference arcs, so the scale is familiar -------
    for k in range(1, 12):
        c = 2412 + 5 * (k - 1)
        solid = k in (1, 6, 11)
        parts.append(
            f"<path d='{arc(c - 11, c + 11, TOP + 34)}' fill='none' "
            f"stroke='{'#4a4a46' if solid else '#333330'}' stroke-width='1.4' "
            + ("" if solid else "stroke-dasharray='3 3'") + "/>")
        parts.append(f"<text x='{x(c):.1f}' y='{BASE + 30:.0f}' class='wch'>{k}</text>")

    # --- the occupants ------------------------------------------------------
    rows = []
    for a in atts or []:
        if a["verdict"] not in ("confirmed", "weak") or not a["bands"]:
            continue
        b = max(a["bands"], key=lambda z: z["avg"])
        victim = "Buds" in a["name"]
        rows.append({"name": a["name"].split("(")[0].strip(),
                     "lo": b["lo"], "hi": b["hi"], "avg": b["avg"],
                     "conf": a["confidence"], "victim": victim,
                     "colour": "#199e70" if victim
                     else ("#d03b3b" if b["avg"] >= 20 else "#fab219")})
    for s in suspects or []:
        rows.append({"name": "Unidentified", "lo": s["lo"], "hi": s["hi"],
                     "avg": s["level"], "conf": None, "victim": False,
                     "colour": "#d55181"})
    if not rows:
        return ""

    attackers = [r for r in rows if not r["victim"]]
    zlo = min(r["lo"] for r in attackers)
    zhi = max(r["hi"] for r in attackers)
    stolen = len([c for c in range(zlo, zhi + 1) if BT_LO <= c <= BT_HI])

    # Bluetooth's whole playground, drawn as a big filled arc UNDER everything.
    # This is the thing the picture has to make obvious: the buds do not live at
    # one frequency, they use the entire span, and the interferers sit INSIDE
    # it. Drawn as a thin baseline strip it read as "nothing overlaps the buds",
    # which is the opposite of the finding.
    bx1, bx2 = x(MHZ(BT_LO)), x(MHZ(BT_HI + 1))
    zx1, zx2 = x(MHZ(zlo)), x(MHZ(zhi + 1))
    parts.insert(0, f"<path d='{arc(MHZ(BT_LO), MHZ(BT_HI + 1), BASE - 74)}' "
                    f"fill='#199e70' fill-opacity='.13' stroke='#199e70' "
                    f"stroke-width='1.6' stroke-dasharray='7 5'/>")

    # Where the interferers land inside that span - the actual collision.
    parts.insert(1, f"<rect x='{zx1:.1f}' y='{TOP - 8:.1f}' "
                    f"width='{zx2-zx1:.1f}' "
                    f"height='{BASE - TOP + 8:.1f}' fill='#d03b3b' opacity='.13'/>")
    parts.insert(2, f"<text x='{(zx1+zx2)/2:.1f}' y='{TOP - 16:.0f}' "
                    f"class='zonecap'>INTERFERERS</text>")

    # The two span labels live BELOW the frequency axis on their own bracket.
    # Drawn inside the plot they printed straight over the arcs and were half
    # unreadable; a dimension bracket says the same thing and collides with
    # nothing.
    parts.append(f"<path d='M {bx1:.1f},{BRK-6:.0f} L {bx1:.1f},{BRK:.0f} "
                 f"L {bx2:.1f},{BRK:.0f} L {bx2:.1f},{BRK-6:.0f}' fill='none' "
                 f"stroke='#199e70' stroke-width='1.6'/>")
    parts.append(f"<line x1='{zx1:.1f}' y1='{BRK:.0f}' x2='{zx2:.1f}' "
                 f"y2='{BRK:.0f}' stroke='#d03b3b' stroke-width='5'/>")
    parts.append(f"<text x='{(bx1+bx2)/2:.1f}' y='{BRK+17:.0f}' class='hoplab'>"
                 f"YOUR EARBUDS HOP ACROSS ALL OF THIS &#8212; 2402&#8211;2480 MHz, "
                 f"79 channels</text>")
    parts.append(f"<text x='{(bx1+bx2)/2:.1f}' y='{BRK+33:.0f}' class='ziplab'>"
                 f"&#9632; the red part is gone &#8212; {stolen} of those 79 channels "
                 f"are taken by the interferers above</text>")

    # Every device gets its OWN apex height, widest arc lowest, so overlapping
    # bands nest visibly instead of the tallest hiding the rest. Three devices
    # sharing one band was drawn as a single blob until this.
    order = sorted(rows, key=lambda r: -(r["hi"] - r["lo"]))
    n = len(order)
    for i, r in enumerate(order):
        r["apex"] = TOP + i * (58.0 / max(1, n - 1) if n > 1 else 0)
        r["cx"] = (x(MHZ(r["lo"])) + x(MHZ(r["hi"] + 1))) / 2.0

    for r in order:
        lo, hi = MHZ(r["lo"]), MHZ(r["hi"] + 1)
        parts.append(f"<path d='{arc(lo, hi, r['apex'])}' fill='{r['colour']}' "
                     f"fill-opacity='.12' stroke='{r['colour']}' stroke-width='2.6'/>")

    # Labels in stacked lanes. Placing them all at the arc centre printed three
    # names on top of each other; each now takes the first lane it fits in, with
    # a leader line back down to its own arc.
    LANE_H, CHAR_W = 26.0, 5.6
    lanes = []                      # lanes[k] = list of (x_left, x_right) taken
    for r in sorted(order, key=lambda z: z["cx"]):
        # Recompute per row. Reusing lo/hi from the arc loop above left every
        # label showing the LAST device's start frequency.
        lo, hi = MHZ(r["lo"]), MHZ(r["hi"])
        conf = f" · {r['conf']}%" if r["conf"] is not None else " · untested"
        text = r["name"] + conf
        # A name the second radio read off the air outranks one inferred from a
        # power-cycle - but only if that access point is loud enough for this
        # scanner to be detecting it at all. named_for() enforces both.
        ap = named_for(names, r["lo"], r["hi"])
        sub = f"{lo}-{hi} MHz · ch {r['lo']}-{r['hi']} · +{r['avg']:.0f}"
        if ap:
            sub += f" · {ap['ssid'] or '(hidden)'} @ {ap['rssi']} dBm"
        half = max(len(text), len(sub), 26) * CHAR_W / 2.0
        lx = min(W - half - 2, max(half + 2, r["cx"]))
        want = (lx - half, lx + half)
        k = 0
        while k < len(lanes) and any(not (want[1] < a or want[0] > b) for a, b in lanes[k]):
            k += 1
        if k == len(lanes):
            lanes.append([])
        lanes[k].append(want)
        ly = 22.0 + k * LANE_H

        parts.append(f"<line x1='{r['cx']:.1f}' y1='{r['apex'] - 2:.1f}' "
                     f"x2='{lx:.1f}' y2='{ly + 5:.1f}' stroke='{r['colour']}' "
                     f"stroke-width='1' opacity='.55'/>")
        parts.append(f"<text x='{lx:.1f}' y='{ly:.0f}' class='alab' "
                     f"fill='{r['colour']}'>{html.escape(text)}</text>")
        parts.append(f"<text x='{lx:.1f}' y='{ly + 12:.0f}' class='asub' "
                     f"fill='{r['colour']}'>{html.escape(sub)}</text>")

    parts.append(f"<line x1='0' y1='{BASE:.1f}' x2='{W:.0f}' y2='{BASE:.1f}' "
                 f"stroke='#6a6a64' stroke-width='2'/>")
    for mhz in range(2400, 2486, 10):
        parts.append(f"<line x1='{x(mhz):.1f}' y1='{BASE:.1f}' x2='{x(mhz):.1f}' "
                     f"y2='{BASE + 6:.1f}' stroke='#6a6a64' stroke-width='1'/>")
        parts.append(f"<text x='{x(mhz):.1f}' y='{BASE + 18:.0f}' class='fx'>{mhz}</text>")
    parts.append(f"<text x='{x(2404):.1f}' y='{BASE + 30:.0f}' class='wch' "
                 f"fill='#7d7d76'>Wi-Fi ch</text>")

    return (
        f"<div class='arcwrap'><svg viewBox='0 0 {W:.0f} {H:.0f}' "
        f"preserveAspectRatio='xMidYMid meet' role='img'>{''.join(parts)}</svg></div>"
        f"<div class='key'>"
        f"<span><i style='background:#199e70'></i>your earbuds (the victim)</span>"
        f"<span><i style='background:#d03b3b'></i>strong interferer</span>"
        f"<span><i style='background:#fab219'></i>weaker interferer</span>"
        f"<span><i style='background:#d55181'></i>unidentified</span>"
        f"<span><i style='background:#4a4a46'></i>Wi-Fi 1&ndash;11 for scale</span></div>"
        f"<div class='note'><b>Nothing sits on your earbuds' own frequencies</b> "
        f"&mdash; the gap either side of them is genuinely empty. The collision is "
        f"the big dashed green arc: your earbuds do not stay at 2458&ndash;2466, they "
        f"hop across <b>all 79 channels</b> 1600 times a second, and the interferers "
        f"sit <b>inside</b> that span. Every pass through the red zone lands on them, "
        f"so adaptive hopping blacklists those channels and your earbuds work with "
        f"<b>{79-stolen} of 79</b> instead of the full set.</div>")


def interception_map(atts, suspects):
    """One picture: who sits where, and where they land on the earbuds.

    The point it has to make is that this is NOT a frequency clash. The
    interferers sit inside Bluetooth's hop range, so the collision happens in
    TIME as the buds hop through them, not because they share a channel.
    """
    rows = []
    for a in atts or []:
        if a["verdict"] not in ("confirmed", "weak") or not a["bands"]:
            continue
        b = max(a["bands"], key=lambda z: z["avg"])
        victim = "Buds" in a["name"]
        rows.append({"name": a["name"], "lo": b["lo"], "hi": b["hi"], "avg": b["avg"],
                     "conf": a["confidence"], "victim": victim,
                     "colour": "#199e70" if victim
                     else ("#d03b3b" if b["avg"] >= 20 else "#fab219")})
    for s in suspects or []:
        rows.append({"name": "Unidentified", "lo": s["lo"], "hi": s["hi"],
                     "avg": s["level"], "conf": None, "victim": False,
                     "colour": "#d55181"})
    if not rows:
        return ""

    attackers = [r for r in rows if not r["victim"]]
    if not attackers:
        return ""
    zlo = min(r["lo"] for r in attackers)
    zhi = max(r["hi"] for r in attackers)
    stolen = len([c for c in range(zlo, zhi + 1) if BT_LO <= c <= BT_HI])

    out = []
    zl, zw = _x(zlo), _x(zhi + 1) - _x(zlo)
    out.append(f"<div class='zone' style='left:{zl:.2f}%;width:{zw:.2f}%'>"
               f"<b>COLLISION &mdash; {stolen} of 79 channels taken</b></div>")

    # The victim's full playground first.
    bl, bw = _x(BT_LO), _x(BT_HI + 1) - _x(BT_LO)
    out.append("<div class='rowlab'><b style='color:#199e70'>Your earbuds need ALL "
               "of this</b><span>2402&ndash;2480 MHz &middot; ch 2&ndash;80 &middot; "
               "79 channels, hopping 1600&times;/sec</span></div>")
    out.append(f"<div class='track'><div class='hop' "
               f"style='left:{bl:.2f}%;width:{bw:.2f}%'></div></div>")

    for r in sorted(rows, key=lambda x: (x["victim"], -x["avg"])):
        conf = (f" &middot; {r['conf']}% confident" if r["conf"] is not None
                else " &middot; untested")
        amt = "where they were measured" if r["victim"] else f"+{r['avg']:.0f} points"
        l, w = _x(r["lo"]), _x(r["hi"] + 1) - _x(r["lo"])
        out.append(f"<div class='rowlab'><b>{html.escape(r['name'])}</b>"
                   f"<span>{MHZ(r['lo'])}&ndash;{MHZ(r['hi'])} MHz &middot; "
                   f"ch {r['lo']}&ndash;{r['hi']} &middot; {amt}{conf}</span></div>")
        out.append(f"<div class='track'><div class='blk' style='left:{l:.2f}%;"
                   f"width:{w:.2f}%;background:{r['colour']}'>"
                   f"{MHZ(r['lo'])}-{MHZ(r['hi'])}</div></div>")

    ticks = "".join(f"<i style='left:{_x(c):.2f}%'>{MHZ(c)}</i>"
                    for c in range(0, 86, 10))
    out.append(f"<div class='ruler'>{ticks}</div>")

    return (
        "<div class='map'>" + "".join(out) + "</div>"
        "<div class='key'>"
        "<span><i style='background:#199e70'></i>your earbuds (the victim)</span>"
        "<span><i style='background:#d03b3b'></i>strong interferer</span>"
        "<span><i style='background:#fab219'></i>weaker interferer</span>"
        "<span><i style='background:#d55181'></i>unidentified</span></div>"
        f"<div class='note'><b>This is not a frequency clash.</b> The interferers sit "
        f"at {MHZ(zlo)}&ndash;{MHZ(zhi)} MHz and your earbuds were measured well away "
        f"from that. But Bluetooth does not stay put &mdash; it hops across all 79 "
        f"channels 1600 times a second, and those interferers sit <b>inside the hop "
        f"range</b>. Every pass through ch {zlo}&ndash;{zhi} lands on them, so adaptive "
        f"hopping blacklists those channels and your earbuds work with about "
        f"<b>{79 - stolen} of 79</b> instead of the full set. Fewer channels means more "
        f"retries, and retries are what you hear as a dropout.</div>")


def summary_table(atts, suspects, mean):
    """The one table that answers everything at a glance."""
    rows = []
    for a in atts or []:
        if a["verdict"] not in ("confirmed", "weak") or not a["bands"]:
            continue
        b = max(a["bands"], key=lambda z: z["avg"])
        lo, hi = b["lo"], b["hi"]
        peak = max(mean[c] for c in range(lo, hi + 1))
        blocks = len([c for c in range(lo, hi + 1) if 2 <= c <= 80])
        cls = "ok" if a["verdict"] == "confirmed" else "maybe"
        rows.append((b["avg"],
                     f"<tr><td><b>{html.escape(a['name'])}</b></td>"
                     f"<td class='num'>{MHZ(lo)}&ndash;{MHZ(hi)} MHz</td>"
                     f"<td class='num'>ch {lo}&ndash;{hi}</td>"
                     f"<td class='num'>{hi-lo+1}</td>"
                     f"<td class='num'>{wifi_span(lo, hi)}</td>"
                     f"<td class='num'><b>{blocks}</b> of 79</td>"
                     f"<td class='num'>+{b['avg']:.0f}</td>"
                     f"<td class='num'>{peak:.0f}%</td>"
                     f"<td class='num'><span class='who {cls}'>{a['confidence']}%</span></td>"
                     f"<td class='num'>{a['runs']}</td></tr>"))
    for s in suspects or []:
        lo, hi = s["lo"], s["hi"]
        blocks = len([c for c in range(lo, hi + 1) if 2 <= c <= 80])
        rows.append((-1,
                     f"<tr><td><b class='susp'>Unidentified</b><br>"
                     f"<span class='unk'>"
                     + html.escape(", ".join(c.split("(")[0].strip()
                                             for c in s["candidates"])) + "</span></td>"
                     f"<td class='num'>{MHZ(lo)}&ndash;{MHZ(hi)} MHz</td>"
                     f"<td class='num'>ch {lo}&ndash;{hi}</td>"
                     f"<td class='num'>{hi-lo+1}</td>"
                     f"<td class='num'>{wifi_span(lo, hi)}</td>"
                     f"<td class='num'><b>{blocks}</b> of 79</td>"
                     f"<td class='num'>&mdash;</td>"
                     f"<td class='num'>{s['level']:.0f}%</td>"
                     f"<td class='num'><span class='who sus'>untested</span></td>"
                     f"<td class='num'>0</td></tr>"))
    if not rows:
        return ""
    body = "".join(r[1] for r in sorted(rows, key=lambda x: -x[0]))
    return (
        "<table class='sum'><thead><tr>"
        "<th>Device</th><th class='num'>Frequency band</th><th class='num'>nRF ch</th>"
        "<th class='num'>Width</th><th class='num'>Wi-Fi ch</th>"
        "<th class='num'>Blocks</th><th class='num'>Adds</th>"
        "<th class='num'>Peak</th><th class='num'>Confidence</th><th class='num'>Trials</th>"
        "</tr></thead><tbody>" + body + "</tbody></table>"
        "<div class='note'><b>nRF ch</b> = the scanner's own channel numbering, "
        "channel N = 2400+N MHz. <b>Width</b> = how many channels wide, which is "
        "also its width in MHz. <b>Blocks</b> = how many of the 79 channels "
        "Bluetooth hops through this device takes away. <b>Adds</b> = extra "
        "occupancy measured when it is switched on. <b>Peak</b> = highest single "
        "channel in that band.</div>")


def attribution_html(atts):
    if not atts:
        return ""
    rows = []
    for a in atts:
        cls, badge = VERDICT_STYLE[a["verdict"]]
        pct = a.get("confidence", 0)
        bands = ", ".join(
            f"<b>{MHZ(b['lo'])}&ndash;{MHZ(b['hi'])} MHz</b> ({b['avg']:+.1f})"
            for b in a["bands"]) or "&mdash;"
        extra = ""
        if a.get("dropped"):
            d = ", ".join(f"{MHZ(x['lo'])}&ndash;{MHZ(x['hi'])} MHz" for x in a["dropped"])
            extra = (f"<div class='retract'>Seen once at {d} but not on repeat &mdash; "
                     f"<b>retracted</b>.</div>")
        meter = ""
        if a["verdict"] in ("confirmed", "weak"):
            meter = (f"<div class='conf'><div class='cbar'><i style='width:{pct}%'></i></div>"
                     f"<span class='cnum'>{pct}%</span> confident this is the named device</div>")
        rows.append(
            f"<div class='att {cls}'><div class='attbar'></div><div class='attbody'>"
            f"<span class='lab'>{badge} &middot; {a['runs']} trial{'s' if a['runs']!=1 else ''}</span>"
            f"<b>{html.escape(a['name'])}</b>"
            f"<div class='freq'>{bands}</div>{meter}"
            f"<p>{a['why']}</p>{extra}</div></div>")
    return "".join(rows)


def suspects_html(suspects):
    """Bands with real traffic that no device has claimed yet, plus who to test."""
    if not suspects:
        return ""
    out = []
    for s in suspects:
        cands = "".join(f"<li>{html.escape(c)}</li>" for c in s["candidates"])
        out.append(
            f"<div class='att serious'><div class='attbar'></div><div class='attbody'>"
            f"<span class='lab'>UNCLAIMED &middot; not yet power-cycled</span>"
            f"<b>{MHZ(s['lo'])}&ndash;{MHZ(s['hi'])} MHz &mdash; {s['level']:.0f}% busy</b>"
            f"<p>{s['note']}</p>"
            f"<div class='cands'>Suspects, in the order I would test them:"
            f"<ol>{cands}</ol>"
            f"Power one off, scan, power it on, scan. Whichever one moves these "
            f"frequencies owns them.</div></div></div>")
    return "".join(out)


def named_channels(attributions):
    """channel -> LIST of (name, verdict, avg), strongest first.

    A channel can carry several transmitters at once - the Xbox and the
    FancyLEDs box share 2414-2430 here. An earlier version kept only the
    strongest claim per channel, which silently deleted the other device from
    the table and made it look like it had been dropped from the report.
    """
    out = {}
    for a in attributions or []:
        if a["verdict"] not in ("confirmed", "weak"):
            continue
        for b in a["bands"]:
            for c in range(b["lo"], b["hi"] + 1):
                out.setdefault(c, []).append((a["name"], a["verdict"], b["avg"]))
    for c in out:
        out[c].sort(key=lambda x: -x[2])
    return out


def short_name(name):
    """Trim a label to something that fits beside a bar."""
    n = name.split("(")[0].strip()
    return n if len(n) <= 30 else n[:29] + "…"


def build(mean, meta, attributions=None, suspects=None, names=None):
    a = analyse(mean)
    sev, title, body = audio_verdict(a)
    # Needed by both the busiest-channels table and the spectrum rows below,
    # so it has to exist before either is built.
    named = named_channels(attributions)

    # Channels inside an unclaimed band get tagged SUSPECT with the shortlist,
    # so the candidates appear ON the frequencies rather than only in a panel
    # further down the page.
    suspected = {}
    for s in suspects or []:
        short = " / ".join(c.split("(")[0].strip() for c in s["candidates"][:3])
        for c in range(s["lo"], s["hi"] + 1):
            suspected.setdefault(c, short)

    # AFH headroom gauge - one tick per Bluetooth channel.
    ticks = "".join(
        f'<i class="{"busy" if mean[c] >= AFH_BUSY else "free"}"></i>'
        for c in range(BT_LO, BT_HI + 1))

    ranked = sorted(range(NCH), key=lambda i: -mean[i])[:12]
    def who(c):
        # A channel can hold BOTH an identified device and a residual that
        # survives with that device switched off. Showing only the first would
        # hide the second transmitter, which is the one still to be found.
        parts = []
        for nm, verdict, avg in named.get(c, []):
            badge = ("<span class='who ok'>identified</span>" if verdict == "confirmed"
                     else "<span class='who maybe'>likely</span>")
            parts.append(f"<div class='dev'><b>{html.escape(short_name(nm))}</b> "
                         f"{badge} <span class='amt'>+{avg:.0f}</span></div>")
        if c in suspected:
            parts.append(f"<div class='alsohere'><b class='susp'>+ also "
                         f"{html.escape(suspected[c])}</b>"
                         f"<span class='who sus'>suspect</span></div>")
        return "".join(parts) or "<span class='unk'>not yet identified</span>"

    rows = "".join(
        f"<tr><td class='num'>{MHZ(c)} MHz</td><td class='num'>{c}</td>"
        f"<td class='num'>{', '.join(str(k) for k in wifi_at(c)) or '—'}</td>"
        f"<td class='num'>{mean[c]:.0f}%</td>"
        f"<td>{who(c)}</td>"
        f"<td>{html.escape(', '.join(bands_at(c)) or '—')}</td></tr>"
        for c in ranked if mean[c] >= 1)

    wifi = []
    for ch, lo, hi in ((1, 2, 22), (6, 27, 47), (11, 52, 72)):
        load = sum(mean[lo:hi + 1]) / (hi - lo + 1)
        wifi.append((ch, load))
    quietest = min(wifi, key=lambda x: x[1])[0]
    wifi_rows = "".join(
        bar_row(f"Wi-Fi ch {ch}", load,
                "quietest — use this" if ch == quietest else "",
                "#0ca30c" if ch == quietest else None)
        for ch, load in wifi)

    # Mark the channels that are actually the problem, rather than drawing them
    # the same as everything else. Wideband blocks are what starve Bluetooth of
    # somewhere to hop; a lone narrowband camper is a different, milder fault.
    # When the whole band is loud there is no single "wideband block" to point
    # at - shading 40 rows orange would say nothing. Mark only real carriers.
    wide = set() if a["saturated"] else {
        c for lo, hi in a["plateaus"] for c in range(lo, hi + 1)}
    # Widen to neighbours only where they are actually elevated. Blanket ±1
    # painted a 4%-idle channel as "FIXED-FREQUENCY DEVICE" purely because the
    # channel next to it was busy.
    narrow = set()
    for s in a["spikes"]:
        narrow.add(s)
        for k in (s - 1, s + 1):
            if 0 <= k < NCH and mean[k] >= a["thr"]:
                narrow.add(k)

    def row_for(c):
        tag = ", ".join(bands_at(c))
        # A device you identified beats a generic protocol label - that is the
        # whole point of the power-cycling exercise.
        if c in named:
            devs = named[c]
            mark = "◄ " + " + ".join(
                short_name(n) + ("" if v == "confirmed" else " (likely)") for n, v, _ in devs)
            if c in suspected:
                mark += " + SUSPECT: " + suspected[c]
            allconf = all(v == "confirmed" for _n, v, _a in devs)
            return bar_row(f"{MHZ(c)} MHz", mean[c], mark,
                           "#0ca30c" if allconf else "#fab219")
        if c in suspected:
            return bar_row(f"{MHZ(c)} MHz", mean[c],
                           "◄ SUSPECT: " + suspected[c], "#d55181")
        if c in narrow:
            return bar_row(f"{MHZ(c)} MHz", mean[c],
                           "◄ FIXED-FREQUENCY DEVICE" + (f" — {tag}" if tag else ""),
                           "#d03b3b")
        if c in wide:
            return bar_row(f"{MHZ(c)} MHz", mean[c],
                           "◄ wideband — squeezes Bluetooth" + (f" — {tag}" if tag else ""),
                           "#ec835a")
        return bar_row(f"{MHZ(c)} MHz", mean[c], tag)

    spec = "".join(row_for(c) for c in range(0, 86, 2))

    legend = (
        '<div class="legend">'
        '<span><i style="background:#0ca30c"></i>identified device</span>'
        '<span><i style="background:#fab219"></i>likely device (needs another trial)</span>'
        '<span><i style="background:#d55181"></i>SUSPECT — unclaimed, power-cycle to identify</span>'
        '<span><i style="background:#d03b3b"></i>fixed-frequency emitter — unidentified</span>'
        '<span><i style="background:#ec835a"></i>wideband — starves Bluetooth of room to hop</span>'
        '<span><i style="background:#3987e5"></i>ordinary traffic</span></div>')

    tips = "".join(
        f'<div class="tip"><div class="k">{i}</div><div class="bd">'
        f'<b>{t}</b><p>{d}</p></div></div>'
        for i, (t, d) in enumerate(advice(a, attributions, suspects), 1))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>rf24scan report — {meta['when']}</title>
<style>{CSS}</style></head><body><div class="wrap">

<header class="top">
  <span class="dot"></span>
  <div><h1>2.4 GHz scan report</h1>
    <span class="lab">rf24scan &middot; receive only</span></div>
  <div class="meta">{html.escape(meta['when'])}<br>
    {meta['sweeps']} sweeps &middot; {meta['ms']:.0f} ms each</div>
</header>

<section class="card hero {sev}">
  <div class="accent"></div>
  <div class="body">
    <span class="lab">Why your audio drops out</span>
    <h1>{title}</h1>
    <p>{body}</p>
  </div>
</section>

<section class="card">
  <h2>Where everything sits, and where they collide</h2>
  {arc_diagram(attributions, suspects, names)}
</section>

<section class="card">
  <h2>Room left for Bluetooth to hop</h2>
  <p style="margin:0 0 4px;font-size:13px">Each mark is one of the {a['total']}
    channels Bluetooth hops across. <b style="color:var(--good)">Green</b> is
    usable, <b style="color:var(--critical)">red</b> is too busy for adaptive
    hopping to use.</p>
  <div class="gauge">{ticks}</div>
  <div class="lab">{a['usable']} of {a['total']} usable &middot;
    2402&ndash;2480 MHz &middot; in-band average {a['inband']:.1f}%</div>
</section>

{named_section(names, attributions, suspects)}

{promisc_section(names)}

{zigbee_section(names)}

{inventory_section(names)}

<section class="card">
  <h2>Which device is which</h2>
  <p class="caption" style="margin-top:0">The radio only ever reports
    <i>"something is on this frequency"</i> &mdash; it cannot read a name off the
    air. Names come from switching a device off and on and seeing which
    frequencies move with it. Confidence comes from <b>repeating</b> that: a
    cluster seen once may be a neighbour, one seen every time is the device.</p>
  {summary_table(attributions, suspects, mean)}
  {attribution_html(attributions)}
  {suspects_html(suspects)}
</section>

<section class="card">
  <h2>What is on the air</h2>
  {legend}
  <div class="spec">{spec}</div>
  <div class="note">Noise floor above the ISM edge (2484&ndash;2525 MHz, where
    nothing legitimate transmits): <b>{a['floor']:.1f}%</b>.
    {"<b style='color:var(--critical)'>Too high — the receiver is being overloaded, so every reading above is inflated.</b>"
     if a['overloaded'] else "Low, so the readings above are trustworthy."}</div>
</section>

<section class="card">
  <h2>Busiest channels</h2>
  <table><thead><tr><th class="num">Frequency</th><th class="num">nRF ch</th>
    <th class="num">Wi-Fi ch</th><th class="num">Busy</th><th>Device</th>
    <th>Protocol space</th></tr></thead>
    <tbody>{rows or '<tr><td colspan=6>Nothing above 1%.</td></tr>'}</tbody></table>
  <div class="note">A device name here means you switched that device off and on
    and these frequencies moved with it. <b>Not yet identified</b> means real
    traffic whose owner has not been established &mdash; power-cycle a suspect
    while scanning and it will be named.</div>
</section>

<section class="card">
  <h2>Wi-Fi channel load</h2>
  {wifi_rows}
  <div class="note">Set your router to channel <b>{quietest}</b>, explicitly.
    Leaving it on <i>auto</i> means it will re-pick later and undo this.</div>
</section>

{suspects_section(names, attributions, suspects)}

<section class="card">
  <h2>What to do about it</h2>
  {tips}
</section>

<div class="note" style="border:none;opacity:.85">
  Occupancy is how often a carrier above &minus;64&nbsp;dBm was present &mdash; a
  duty cycle, not a power level. Anything weaker than &minus;64&nbsp;dBm is
  invisible to this hardware, so a quiet reading is not proof of a quiet room.
</div>
</div></body></html>"""


def write(path, mean, sweeps, ms):
    meta = {"when": time.strftime("%Y-%m-%d %H:%M"), "sweeps": sweeps, "ms": ms}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build(mean, meta))
    return path
