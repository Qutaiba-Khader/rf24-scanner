#!/usr/bin/env python3
"""
Read the naming radio - the ESP32 running the rfnames firmware.

    python tools/esp32_scan.py --port COM12 --seconds 25
    python tools/esp32_scan.py --port COM12 --seconds 60 --save names.json

WHAT THIS IS FOR
----------------
The nRF24 measures ENERGY and can never say what something is: a 1-bit detector
at a fixed -64 dBm reports "busy", never "who". The ESP32 is blind to energy but
reads IDENTITY - SSID, BSSID, device name, MAC - and a real RSSI in dBm.

Wi-Fi channel k centres at 2412+5(k-1) MHz and is 20 MHz wide, so every named
access point maps onto an exact nRF channel span. That is what makes the two
instruments combine: energy the nRF24 sees that NO named access point above its
-64 dBm floor can account for is, by elimination, a proprietary 2.4 GHz emitter.

THE ONE THING TO KEEP STRAIGHT
A Wi-Fi scan sees BEACONS, not traffic. An access point hammering the band and
an idle one beacon identically, about ten times a second. This tool says WHO is
there. Only the nRF24 says HOW MUCH of the air they take.

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

# The USB-serial bridges these boards actually ship with.
BRIDGE_VIDS = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH340"}

AUTH = ["open", "WEP", "WPA", "WPA2", "WPA/WPA2", "enterprise", "WPA3", "WPA2/3"]

# The nRF24's RPD threshold. Anything weaker than this cannot be the source of
# energy the scanner is measuring, however suspicious its name looks.
RPD_FLOOR_DBM = -64


def wifi_span(ch):
    """nRF channel span of a 20 MHz Wi-Fi channel. ch 1 -> (2, 22)."""
    c = 12 + 5 * (ch - 1)
    return c - 10, c + 10


def find_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    for p in ports:
        if (p.vid or 0) in BRIDGE_VIDS:
            return p.device
    raise SystemExit(
        "No CP210x/CH340 device found.\n"
        "Ports seen: " + (", ".join(p.device for p in ports) or "none") + "\n"
        "Pass one explicitly with --port COM12."
    )


def collect(port, seconds, raw=False):
    print(f"Opening {port} ...", flush=True)
    ser = serial.Serial(port, 115200, timeout=1)
    time.sleep(0.4)
    ser.reset_input_buffer()
    ser.write(b"?\n")

    wifi, ble, control = {}, {}, []
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            line = ser.readline().decode("utf-8", "replace").rstrip()
        except Exception as exc:                     # noqa: BLE001
            print(f"read error: {exc}")
            break
        if not line:
            continue
        if raw:
            print("  " + line, flush=True)

        if line.startswith("W "):
            # W <rssi> <ch> <bssid> <auth> <ssid...>  - SSID is the rest of the
            # line because it is the only field that can contain spaces.
            p = line.split(" ", 5)
            if len(p) < 5:
                continue
            try:
                rssi, ch, auth = int(p[1]), int(p[2]), int(p[4])
            except ValueError:
                continue
            wifi[p[3]] = {"bssid": p[3], "rssi": rssi, "ch": ch, "auth": auth,
                          "ssid": p[5] if len(p) > 5 else ""}
        elif line.startswith("B "):
            p = line.split(" ", 4)
            if len(p) < 4:
                continue
            try:
                rssi = int(p[1])
            except ValueError:
                continue
            mac = p[3]
            name = p[4] if len(p) > 4 else ""
            prev = ble.get(mac)
            # Keep the strongest reading, and never lose a name to a later
            # nameless advert - most adverts carry no name at all.
            ble[mac] = {"mac": mac,
                        "rssi": max(rssi, prev["rssi"]) if prev else rssi,
                        "name": name or (prev["name"] if prev else "")}
        else:
            control.append(line)
            if not raw:
                print("  " + line, flush=True)

    try:
        ser.write(b"H\n")
        ser.flush()
        time.sleep(0.2)
    except Exception:
        pass
    ser.close()
    return list(wifi.values()), list(ble.values()), control


def report(wifi, ble):
    print()
    print("=" * 72)
    print("NAMED NEIGHBOURS")
    print("=" * 72)

    if not wifi:
        print("\nNo access points found at all. Either the firmware is not the "
              "rfnames build,\nor the board is not scanning - send 'W' to force one.")
    else:
        print(f"\n{len(wifi)} ACCESS POINTS\n")
        print(f"{'ch':<4}{'RSSI':>7}  {'occupies':<16}{'security':<11}"
              f"{'Pico can see it?':<18}SSID")
        print("-" * 100)
        for a in sorted(wifi, key=lambda x: (x["ch"], -x["rssi"])):
            lo, hi = wifi_span(a["ch"])
            vis = "YES" if a["rssi"] > RPD_FLOOR_DBM else "no - too weak"
            print(f"{a['ch']:<4}{a['rssi']:>6}d  {2400+lo}-{2400+hi} MHz  "
                  f"{AUTH[a['auth']] if a['auth'] < len(AUTH) else a['auth']:<11}"
                  f"{vis:<18}{a['ssid'] or '(hidden)'}")

    if ble:
        print(f"\n{len(ble)} BLE DEVICES (strongest 15)\n")
        print(f"{'RSSI':>7}  {'MAC':<14}{'Pico can see it?':<18}name")
        print("-" * 72)
        for b in sorted(ble, key=lambda x: -x["rssi"])[:15]:
            vis = "YES" if b["rssi"] > RPD_FLOOR_DBM else "no - too weak"
            print(f"{b['rssi']:>6}d  {b['mac']:<14}{vis:<18}{b['name'] or '(no name)'}")

    loud = [a for a in wifi if a["rssi"] > RPD_FLOOR_DBM]
    print("\n" + "-" * 72)
    print("WHAT THIS MEANS FOR THE nRF24 MEASUREMENTS")
    if loud:
        print(f"  {len(loud)} access point(s) are above the nRF24's {RPD_FLOOR_DBM} dBm")
        print("  floor, so they CAN account for measured energy:")
        for a in sorted(loud, key=lambda x: -x["rssi"]):
            lo, hi = wifi_span(a["ch"])
            print(f"    {a['ssid'] or '(hidden)':<26} ch {a['ch']:<3} "
                  f"{2400+lo}-{2400+hi} MHz  {a['rssi']} dBm")
    else:
        print(f"  NOT ONE access point is above the nRF24's {RPD_FLOOR_DBM} dBm floor.")
        print("  So any strong energy the scanner measures is NOT coming from a")
        print("  Wi-Fi access point. By elimination it is a proprietary 2.4 GHz")
        print("  emitter - an LED controller, a dongle, an RF remote.")
    print()
    print("  Remember a Wi-Fi scan sees BEACONS, not traffic: a busy access point")
    print("  and an idle one look identical here. Only the nRF24 measures how much")
    print("  of the air is actually being used.")
    print("  Both radios must sit in the SAME PLACE for these numbers to combine.")


def main():
    ap = argparse.ArgumentParser(
        description="Capture named Wi-Fi and BLE neighbours from the ESP32.")
    ap.add_argument("--port")
    ap.add_argument("--seconds", type=int, default=25,
                    help="one full cycle is ~11s, so 25 gets two (default 25)")
    ap.add_argument("--raw", action="store_true", help="dump raw lines")
    ap.add_argument("--save", metavar="FILE", help="write the result as JSON")
    ap.add_argument("--label", metavar="TEXT", default="",
                    help="where the scanner was, or what was powered on")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    args = ap.parse_args()

    if args.list:
        for p in list_ports.comports():
            tag = BRIDGE_VIDS.get(p.vid or 0, "")
            print(f"  {p.device:<8} {p.description}" + (f"   <- {tag}" if tag else ""))
        return

    wifi, ble, control = collect(find_port(args.port), args.seconds, args.raw)
    report(wifi, ble)

    if args.save:
        json.dump({"tool": "rfnames", "label": args.label,
                   "seconds": args.seconds, "wifi": wifi, "ble": ble,
                   "control": control[:40]},
                  open(args.save, "w"), indent=1)
        print(f"\nSaved: {args.save}")


if __name__ == "__main__":
    main()
