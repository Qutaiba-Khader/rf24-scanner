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


def advice(a):
    out = []
    if a["widest"] >= 12:
        out.append(("The interference is <b>wideband</b>, and that is the kind "
                    "that hurts audio", "A wide block leaves Bluetooth nowhere "
                    "to hop. Narrow interferers it can dodge; this it cannot."))
    out.append(("Suspect USB 3.0 first",
                "USB 3.0 ports, cables and external drives radiate broadband "
                "noise straight across 2.4 GHz. It is wideband, so it defeats "
                "adaptive hopping, and your dongle is usually plugged in right "
                "next to it. Move the dongle to a <b>USB 2.0</b> port on a short "
                "extension cable, away from the case."))
    out.append(("Set your Wi-Fi channel by hand",
                "Pick the quietest of 1/6/11 from the table below and set it "
                "explicitly. Do not leave the router on <i>auto</i> - it will "
                "re-pick on its own schedule and undo the change."))
    out.append(("Move Wi-Fi to 5 GHz where you can",
                "That vacates the band rather than competing for it. Usually the "
                "single most effective change."))
    out.append(("Keep line of sight",
                "Your own body between the earbuds and the transmitter costs "
                "more signal than most interference does."))
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


def build(mean, meta):
    a = analyse(mean)
    sev, title, body = audio_verdict(a)

    # AFH headroom gauge - one tick per Bluetooth channel.
    ticks = "".join(
        f'<i class="{"busy" if mean[c] >= AFH_BUSY else "free"}"></i>'
        for c in range(BT_LO, BT_HI + 1))

    ranked = sorted(range(NCH), key=lambda i: -mean[i])[:12]
    rows = "".join(
        f"<tr><td class='num'>{MHZ(c)} MHz</td><td class='num'>ch {c}</td>"
        f"<td class='num'>{mean[c]:.0f}%</td>"
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
        '<span><i style="background:#d03b3b"></i>fixed-frequency device — '
        'the thing to hunt down</span>'
        '<span><i style="background:#ec835a"></i>wideband — this is what starves '
        'Bluetooth of room to hop</span>'
        '<span><i style="background:#3987e5"></i>ordinary traffic</span></div>')

    tips = "".join(
        f'<div class="tip"><div class="k">{i}</div><div class="bd">'
        f'<b>{t}</b><p>{d}</p></div></div>'
        for i, (t, d) in enumerate(advice(a), 1))

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
  <h2>Room left for Bluetooth to hop</h2>
  <p style="margin:0 0 4px;font-size:13px">Each mark is one of the {a['total']}
    channels Bluetooth hops across. <b style="color:var(--good)">Green</b> is
    usable, <b style="color:var(--critical)">red</b> is too busy for adaptive
    hopping to use.</p>
  <div class="gauge">{ticks}</div>
  <div class="lab">{a['usable']} of {a['total']} usable &middot;
    2402&ndash;2480 MHz &middot; in-band average {a['inband']:.1f}%</div>
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
  <table><thead><tr><th class="num">Frequency</th><th class="num">Channel</th>
    <th class="num">Busy</th><th>Belongs to</th></tr></thead>
    <tbody>{rows or '<tr><td colspan=4>Nothing above 1%.</td></tr>'}</tbody></table>
</section>

<section class="card">
  <h2>Wi-Fi channel load</h2>
  {wifi_rows}
  <div class="note">Set your router to channel <b>{quietest}</b>, explicitly.
    Leaving it on <i>auto</i> means it will re-pick later and undo this.</div>
</section>

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
