#!/usr/bin/env python3
"""
nRF24 packet sniffer - find the proprietary gadgets that no scan can name.

    python tools/sniff.py --port COM19 --seconds 60
    python tools/sniff.py --port COM19 --seconds 60 --bw 2 --channels 0,83

WHAT THIS IS FOR
----------------
Wi-Fi and BLE scans only find devices that announce themselves. A wireless
mouse dongle, an LED remote, an RF doorbell - none of them do. But most of them
use the nRF24 family or a clone, and firmware v1.3.0+ can be told to listen for
their packets instead of measuring energy.

WHY THE FILTERING MATTERS MORE THAN THE CAPTURE
-----------------------------------------------
CRC is disabled - it has to be, since we do not know the real address - so the
receiver accepts anything that vaguely resembles a preamble. Raw output is
therefore mostly NOISE, and on a quiet band it is entirely noise.

The discriminator is repetition. A real transmitter sends the same address
every single packet; noise never repeats. So this tool ignores everything seen
once and reports only prefixes that came back. A first run showing "412 packets,
0 repeats" means nothing was transmitting - not that the sniffer failed.

HOW TO ACTUALLY CATCH YOUR MOUSE
--------------------------------
1. Put the scanner within 10-20 cm of the USB DONGLE, not the mouse.
2. Start this tool.
3. MOVE THE MOUSE CONTINUOUSLY for the whole capture. A mouse that is not
   moving transmits almost nothing, and an idle dongle is silent.
4. Most mice run at 2 Mbps: try --bw 2, then --bw 1.

Needs pyserial:  pip install pyserial
"""

import argparse
import collections
import json
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is not installed. Run:  pip install pyserial")

# How many leading bytes identify a transmitter. Five is the nRF24's usual
# address width; using fewer collides, using more splits one device into many
# when the payload varies.
PREFIX = 5

# Seen once is noise. The whole point of this tool is that a real device
# repeats itself.
MIN_REPEATS = 3


def collect(port, seconds, bw, lo, hi, raw=False):
    print(f"Opening {port} ...", flush=True)
    ser = serial.Serial(port, 115200, timeout=1)
    time.sleep(0.4)
    ser.reset_input_buffer()
    ser.write(b"?\n")
    time.sleep(0.4)
    if bw is not None:
        ser.write(f"B{bw}\n".encode())
        time.sleep(0.3)
    ser.write(f"C{lo},{hi}\n".encode())
    time.sleep(0.3)
    ser.write(b"G\n")
    time.sleep(0.2)
    ser.write(b"M1\n")

    pkts, control = [], []
    t0 = time.time()
    last = 0
    while time.time() - t0 < seconds:
        try:
            line = ser.readline().decode("utf-8", "replace").rstrip()
        except Exception as exc:                     # noqa: BLE001
            print(f"read error: {exc}")
            break
        if not line:
            continue
        if line.startswith("N "):
            p = line.split()
            if len(p) >= 3:
                pkts.append((int(p[1]), p[2]))
            if raw:
                print("  " + line)
        else:
            control.append(line)
            if not raw:
                print("  " + line, flush=True)
        done = int(time.time() - t0)
        if done != last and not raw:
            last = done
            print(f"  ...{done}/{seconds}s, {len(pkts)} packets", end="\r", flush=True)

    # Back to sweep mode, then halt, before letting the port go.
    try:
        ser.write(b"M0\n"); ser.flush(); time.sleep(0.25)
        ser.write(b"H\n"); ser.flush(); time.sleep(0.25)
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.close()
    print(" " * 60, end="\r")
    return pkts, control


def report(pkts, seconds):
    print()
    print("=" * 72)
    print("nRF24 PACKET SNIFF")
    print("=" * 72)
    print(f"\n{len(pkts)} raw captures in {seconds}s")

    if not pkts:
        print("\nNothing at all. The board may not be in sniff mode - check that\n"
              "the #info line above says mode=sniff.")
        return []

    by_pre = collections.defaultdict(list)
    for ch, hexs in pkts:
        by_pre[hexs[:PREFIX * 2]].append(ch)

    repeats = {p: chs for p, chs in by_pre.items() if len(chs) >= MIN_REPEATS}
    print(f"{len(by_pre)} distinct {PREFIX}-byte prefixes, "
          f"{len(repeats)} seen {MIN_REPEATS}+ times")

    if not repeats:
        print("\nEVERY capture was unique, which is the signature of pure noise.")
        print("CRC is off, so the receiver accepts anything preamble-shaped; a real")
        print("device would repeat its address on every packet.")
        print("\nThis almost always means nothing nRF24-family was transmitting.")
        print("To catch a mouse dongle:")
        print("  1. put the scanner 10-20 cm from the USB DONGLE, not the mouse")
        print("  2. MOVE THE MOUSE CONTINUOUSLY for the whole capture -")
        print("     a still mouse transmits almost nothing")
        print("  3. try --bw 2 (most mice use 2 Mbps), then --bw 1")
        return []

    print(f"\n{'prefix':<14}{'seen':>6}{'channels':>34}")
    print("-" * 72)
    out = []
    for pre, chs in sorted(repeats.items(), key=lambda x: -len(x[1])):
        u = sorted(set(chs))
        span = (f"{2400+u[0]}-{2400+u[-1]} MHz ({len(u)} ch)" if len(u) > 1
                else f"{2400+u[0]} MHz")
        print(f"{pre:<14}{len(chs):>6}{span:>34}")
        out.append({"prefix": pre, "count": len(chs), "channels": u})

    print("\nA prefix on ONE channel that repeats is a fixed-frequency gadget.")
    print("A prefix appearing across MANY channels is a frequency hopper - which")
    print("is what a wireless mouse or keyboard dongle looks like.")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Listen for nRF24-family packets instead of measuring energy.")
    ap.add_argument("--port", default="COM19")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--bw", type=int, choices=(0, 1, 2), default=2,
                    help="0=250kbps 1=1Mbps 2=2Mbps (default 2 - most mice)")
    ap.add_argument("--channels", default="0,83", metavar="LO,HI",
                    help="nRF channel range (default 0,83 - the whole ISM band)")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--save", metavar="FILE")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.channels.split(","))
    pkts, control = collect(args.port, args.seconds, args.bw, lo, hi, args.raw)
    found = report(pkts, args.seconds)

    if args.save:
        json.dump({"tool": "rf24sniff", "seconds": args.seconds, "bw": args.bw,
                   "channels": [lo, hi], "found": found,
                   "raw_count": len(pkts), "control": control[:30]},
                  open(args.save, "w"), indent=1)
        print(f"\nSaved: {args.save}")


if __name__ == "__main__":
    main()
