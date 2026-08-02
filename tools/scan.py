#!/usr/bin/env python3
"""
rf24scan command-line reader - the whole tool, without a browser.

Opens the Pico's serial port, collects sweeps, and prints a readable report:
what is transmitting, where, and how busy the band is.

    python tools/scan.py                 # auto-detect the port, scan 15s
    python tools/scan.py --seconds 30
    python tools/scan.py --port COM19
    python tools/scan.py --list          # just list serial ports
    python tools/scan.py --raw           # dump raw lines, no analysis

Compare two runs to fingerprint a device:
    python tools/scan.py --seconds 20 --save off.json     # device unplugged
    python tools/scan.py --seconds 20 --save on.json      # device plugged in
    python tools/scan.py --compare off.json on.json

Needs pyserial:  pip install pyserial
"""

import argparse
import json
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is not installed. Run:  pip install pyserial")

NCH = 126
MHZ = lambda c: 2400 + c
RPI_VID = 0x2E8A

# nRF24 channel spans, inclusive. Channel N sits at 2400+N MHz.
BANDS = [
    ("Wi-Fi ch 1", 2, 22), ("Wi-Fi ch 6", 27, 47), ("Wi-Fi ch 11", 52, 72),
    ("Wi-Fi ch 13", 62, 82), ("Bluetooth", 2, 80),
    ("BLE adv 37", 1, 3), ("BLE adv 38", 25, 27), ("BLE adv 39", 79, 81),
]


def bands_at(ch):
    return [n for n, lo, hi in BANDS if lo <= ch <= hi]


def find_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    for p in ports:
        if (p.vid or 0) == RPI_VID:
            return p.device
    raise SystemExit(
        "No Raspberry Pi serial device found.\n"
        "Ports seen: " + (", ".join(p.device for p in ports) or "none") + "\n"
        "Pass one explicitly with --port COM19."
    )


def collect(port, seconds, raw=False, bw=None):
    """Read frames for `seconds`. Returns (frames, control_lines).

    `bw` selects the receiver bandwidth (0=250kbps, 1=1Mbps, 2=2Mbps). It is
    not a cosmetic setting: the rate sets the receiver's IF bandwidth, so a
    *wideband* source delivers more power into a wider receiver while a
    *narrowband* one delivers the same power at every setting. Capturing the
    same scene at all three turns a 1-bit detector into a shape discriminator -
    see `bwtest.py`.
    """
    print(f"Opening {port} ...", flush=True)
    ser = serial.Serial(port, 115200, timeout=0.5)
    time.sleep(0.3)
    ser.reset_input_buffer()
    # The firmware stays silent until the host speaks, so say hello first - then
    # explicitly RESUME. We halt the board on disconnect (so it does not stream
    # into a closed port), which means it is still halted when we come back.
    ser.write(b"?\n")
    time.sleep(0.2)
    if bw is not None:
        ser.write(f"B{bw}\n".encode())
        time.sleep(0.3)
    ser.write(b"G\n")
    time.sleep(0.2)

    frames, control = [], []
    t0 = time.time()
    last = 0
    while time.time() - t0 < seconds:
        try:
            line = ser.readline().decode("utf-8", "replace").strip()
        except Exception as exc:                     # noqa: BLE001
            print(f"read error: {exc}")
            break
        if not line:
            continue
        if raw:
            print("  " + line, flush=True)
        if line.startswith("S "):
            f = parse_frame(line)
            if f:
                frames.append(f)
        else:
            control.append(line)
            if not raw:
                print("  " + line, flush=True)
        done = int(time.time() - t0)
        if done != last and not raw:
            last = done
            print(f"  ...{done}/{seconds}s, {len(frames)} sweeps", end="\r", flush=True)

    # Tell the board to stop before letting go of the port. MicroPython's CDC
    # write BLOCKS once nothing is draining, so a board still streaming into a
    # closed port stalls its own USB stack - and the next attempt to open the
    # port then hangs. Halting first is the difference between a clean
    # disconnect and having to replug the board.
    try:
        ser.write(b"H\n")
        ser.flush()
        time.sleep(0.3)
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.close()
    print(" " * 60, end="\r")
    return frames, control


def parse_frame(line):
    p = line.split(" ")
    if len(p) < 8:
        return None
    try:
        passes, lo, hi, hexs = int(p[2]), int(p[5]), int(p[6]), p[7]
    except ValueError:
        return None
    n = hi - lo + 1
    if not passes or len(hexs) != n * 2:
        return None
    pct = [0.0] * NCH
    try:
        for i in range(n):
            pct[lo + i] = int(hexs[i * 2:i * 2 + 2], 16) / passes * 100
    except ValueError:
        return None
    return {"ms": int(p[4]), "passes": passes, "pct": pct}


def mean_of(frames):
    m = [0.0] * NCH
    for f in frames:
        for i in range(NCH):
            m[i] += f["pct"][i]
    return [v / len(frames) for v in m] if frames else m


def report(frames, control):
    print()
    if not frames:
        print("NO SWEEPS RECEIVED.")
        if any("nRF24 not responding" in c for c in control):
            print("\nThe firmware is running and talking, but the nRF24 is not")
            print("answering on SPI. In order of likelihood:")
            print("  1. the 10uF cap across VCC/GND at the module is missing")
            print("  2. MISO/MOSI swapped (MISO=GP4 pin 6, MOSI=GP7 pin 10)")
            print("  3. VCC on 5V (pin 40) instead of 3V3 (pin 36)")
            print("  4. a cold solder joint")
        elif not control:
            print("\nThe board said nothing at all - it is not running rf24scan,")
            print("or the port is held by another program.")
        return

    m = mean_of(frames)
    ms = sum(f["ms"] for f in frames) / len(frames)
    print(f"{len(frames)} sweeps, {ms:.0f} ms each, "
          f"{frames[0]['passes']} measurements per channel")

    ctrl = sorted(m[84:])
    floor = ctrl[len(ctrl) // 2]
    inband = m[2:81]
    print(f"Noise floor (above the ISM edge): {floor:.1f}%   "
          f"in-band average: {sum(inband)/len(inband):.1f}%")
    if floor > 15:
        print("  WARNING: channels above 2484 MHz should be silent. The receiver")
        print("  is being overloaded, so every number below reads too high.")

    print("\nBUSIEST CHANNELS")
    for c in sorted(range(NCH), key=lambda i: -m[i])[:10]:
        if m[c] < 1:
            break
        bar = "#" * int(m[c] / 100 * 40)
        tags = ", ".join(bands_at(c)) or "no standard protocol here"
        print(f"  {MHZ(c)} MHz  ch{c:<4} {m[c]:5.1f}%  {bar:<40} {tags}")

    print("\nQUIETEST 3 MHz WINDOW")
    best, bs = 2, 1e9
    for c in range(3, 79):
        s = m[c - 1] + m[c] + m[c + 1]
        if s < bs:
            bs, best = s, c
    print(f"  around {MHZ(best)} MHz (channels {best-1}-{best+1}), {bs/3:.1f}% average")

    print("\nWI-FI CHANNEL LOAD")
    for ch, lo, hi in ((1, 2, 22), (6, 27, 47), (11, 52, 72)):
        load = sum(m[lo:hi + 1]) / (hi - lo + 1)
        print(f"  channel {ch:<3} {load:5.1f}%  {'#' * int(load / 100 * 40)}")


def compare(a_path, b_path):
    a = json.load(open(a_path)); b = json.load(open(b_path))
    ma, mb = a["mean"], b["mean"]
    print(f"\nCOMPARING  A={a_path} ({a['sweeps']} sweeps)  "
          f"B={b_path} ({b['sweeps']} sweeps)\n")
    deltas = sorted(((mb[c] - ma[c], c) for c in range(NCH)), reverse=True)
    up = [(d, c) for d, c in deltas if d > 6]
    if not up:
        print("Nothing changed significantly. That device is not transmitting on")
        print("2.4 GHz, is too far away, or only transmits in bursts.")
        return
    print("CHANNELS THAT GOT LOUDER IN B")
    for d, c in up[:10]:
        tags = ", ".join(bands_at(c)) or "no standard protocol here"
        print(f"  {MHZ(c)} MHz  ch{c:<4} +{d:5.1f}%   collides with {tags}")
    chans = sorted(c for _d, c in up)
    span = chans[-1] - chans[0] + 1
    print()
    if span <= 4:
        print(f"All of it sits in a {span}-channel block at {MHZ(chans[0])} MHz:")
        print("a FIXED-FREQUENCY transmitter - LED controller, RF remote, dongle.")
    elif span >= 15 and len(up) >= 8:
        print(f"A ~{span}-channel block: Wi-Fi shaped.")
    else:
        print(f"Spread over {span} channels: hopping or bursty (Bluetooth/BLE).")


def main():
    ap = argparse.ArgumentParser(description="rf24scan command-line reader")
    ap.add_argument("--port")
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--save", metavar="FILE")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--label", metavar="TEXT",
                    help='what was powered on/off for this capture, e.g. "FancyLEDs box ON"')
    ap.add_argument("--html", metavar="FILE", help="write a standalone HTML report")
    ap.add_argument("--open", action="store_true", help="open the HTML report when done")
    ap.add_argument("--bw", type=int, choices=(0, 1, 2), metavar="N",
                    help="receiver bandwidth: 0=250kbps 1=1Mbps 2=2Mbps. "
                         "Wideband sources read higher at 2, narrowband ones "
                         "do not change - see bwtest.py")
    args = ap.parse_args()

    if args.list:
        for p in list_ports.comports():
            vid = f"{p.vid:04x}" if p.vid else "----"
            mark = "  <- Raspberry Pi" if (p.vid or 0) == RPI_VID else ""
            print(f"  {p.device:<8} vid={vid}  {p.description}{mark}")
        return 0

    if args.compare:
        compare(*args.compare)
        return 0

    frames, control = collect(find_port(args.port), args.seconds, args.raw,
                              bw=args.bw)
    if not args.raw:
        report(frames, control)
    if args.save and frames:
        json.dump({"sweeps": len(frames), "mean": mean_of(frames)},
                  open(args.save, "w"))
        print(f"\nSaved to {args.save}")

    # Record it on the timeline. This is what turns "something is on 2462 MHz"
    # into "the FancyLEDs box is on 2462 MHz" - the radio supplies the
    # frequency, the label supplies the name.
    if frames and (args.label or args.save):
        import timeline
        n = timeline.append(mean_of(frames), len(frames), args.label, args.save)
        print(f"Timeline entry #{n}: {args.label or '(unlabelled)'}")

    if args.html:
        if not frames:
            print("\nNo sweeps, so no report to write.")
            return 1
        # Aliased: a bare `import report` here binds the name `report` locally
        # for the whole function, which shadows the module-level report()
        # function above and makes calling it an UnboundLocalError.
        import report as html_report
        ms = sum(f["ms"] for f in frames) / len(frames)
        path = html_report.write(args.html, mean_of(frames), len(frames), ms)
        print(f"\nReport written to {path}")
        if args.open:
            import webbrowser, os
            webbrowser.open("file:///" + os.path.abspath(path).replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
