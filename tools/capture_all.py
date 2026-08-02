#!/usr/bin/env python3
"""
One capture from BOTH radios, at one moment, into one file.

    python tools/capture_all.py --label "after removing the Govee"

WHY THIS EXISTS
---------------
The report is built from the newest `names_*.json`. Before this script, that
file was written by hand from whatever had last been scanned - so a device the
user had physically removed from the room went on being listed as the #1
suspect, because the capture behind the report predated the removal.

A report is only as current as its worst input. This takes everything in one
run, stamps it with the time, and writes a file the report picks up
automatically, so "the room now" and "the report now" cannot drift apart.

WHAT IT COLLECTS
  nRF24 (Pico)   occupancy % per channel - ENERGY, names nothing
  ESP32 scan     Wi-Fi beacons + BLE adverts - IDENTITY + real dBm
  ESP32 monitor  frames/bytes per transmitter - AIRTIME, not presence

Needs pyserial:  pip install pyserial
"""

import argparse
import json
import os
import pathlib
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is not installed. Run:  pip install pyserial")

NCH = 126
CAPDIR = pathlib.Path(os.path.expanduser("~")) / "OneDrive" / "Desktop" / "rf24-captures"


def find(vid, name):
    for p in list_ports.comports():
        if (p.vid or 0) == vid:
            return p.device
    return None


def pico_sweep(port, seconds, out):
    """nRF24 occupancy. Halt on the way out - a board left streaming into a
    closed port stalls its own USB stack."""
    try:
        s = serial.Serial(port, 115200, timeout=1)
    except Exception as exc:                             # noqa: BLE001
        out["pico_error"] = str(exc)
        return
    time.sleep(0.5)
    s.reset_input_buffer()
    s.write(b"?\n"); time.sleep(0.4)
    s.write(b"M0\n"); time.sleep(0.4)                    # sweep, not sniff
    s.write(b"G\n")
    frames = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        line = s.readline().decode("utf-8", "replace").strip()
        if line.startswith("S "):
            p = line.split()
            if len(p) >= 8 and len(p[7]) == NCH * 2:
                passes = int(p[2])
                frames.append([int(p[7][i * 2:i * 2 + 2], 16) / passes * 100
                               for i in range(NCH)])
    try:
        s.write(b"H\n"); s.flush(); time.sleep(0.25)
    except Exception:
        pass
    s.close()
    if frames:
        out["mean"] = [sum(f[i] for f in frames) / len(frames) for i in range(NCH)]
        out["sweeps"] = len(frames)


def esp_capture(port, scan_secs, prom_secs, out):
    """Identity first, then airtime. Both from the same board, back to back."""
    try:
        s = serial.Serial(port, 115200, timeout=1)
    except Exception as exc:                             # noqa: BLE001
        out["esp_error"] = str(exc)
        return
    time.sleep(0.5)
    s.reset_input_buffer()
    s.write(b"?\n"); time.sleep(0.3)
    s.write(b"M0\n"); time.sleep(0.3)
    s.write(b"G\n")

    wifi, ble = {}, {}
    t0 = time.time()
    while time.time() - t0 < scan_secs:
        line = s.readline().decode("utf-8", "replace").strip()
        if line.startswith("W "):
            p = line.split(" ", 5)
            if len(p) >= 5:
                wifi[p[3]] = {"bssid": p[3], "rssi": int(p[1]), "ch": int(p[2]),
                              "auth": int(p[4]), "ssid": p[5] if len(p) > 5 else ""}
        elif line.startswith("B "):
            p = line.split(" ", 4)
            if len(p) >= 4:
                mac, r = p[3], int(p[1])
                nm = p[4] if len(p) > 4 else ""
                prev = ble.get(mac)
                ble[mac] = {"mac": mac, "addrtype": int(p[2]),
                            "rssi": max(r, prev["rssi"]) if prev else r,
                            "name": nm or (prev["name"] if prev else "")}

    s.write(b"M1\n")                                     # promiscuous
    prom = {}
    t0 = time.time()
    while time.time() - t0 < prom_secs:
        line = s.readline().decode("utf-8", "replace").strip()
        if line.startswith("P "):
            p = line.split()
            if len(p) >= 8:
                mac = p[3]
                prom[mac] = {"mac": mac, "rssi": int(p[1]), "ch": int(p[2]),
                             "frames": int(p[4]), "bytes": int(p[5]),
                             "mgmt": int(p[6]), "data": int(p[7])}
    try:
        s.write(b"M0\n"); s.flush(); time.sleep(0.3)
        s.write(b"H\n"); s.flush(); time.sleep(0.25)
    except Exception:
        pass
    s.close()
    out["wifi"] = list(wifi.values())
    out["ble"] = list(ble.values())
    out["promisc"] = list(prom.values())


def main():
    ap = argparse.ArgumentParser(description="Capture both radios in one run.")
    ap.add_argument("--label", default="", help="where the scanner was / what was on")
    ap.add_argument("--seconds", type=int, default=45, help="nRF24 sweep + ESP32 scan")
    ap.add_argument("--promisc", type=int, default=30, help="ESP32 monitor mode")
    ap.add_argument("--pico"), ap.add_argument("--esp")
    ap.add_argument("--out", help="output file (default: next names_NN in rf24-captures)")
    args = ap.parse_args()

    pico = args.pico or find(0x2E8A, "Pico")
    esp = args.esp or find(0x10C4, "ESP32")
    print(f"Pico  : {pico or 'NOT FOUND'}")
    print(f"ESP32 : {esp or 'NOT FOUND'}")
    if not pico and not esp:
        sys.exit("Neither board is connected.")

    res = {}
    threads = []
    if pico:
        threads.append(threading.Thread(target=pico_sweep, args=(pico, args.seconds, res)))
    if esp:
        threads.append(threading.Thread(target=esp_capture,
                                        args=(esp, args.seconds, args.promisc, res)))
    print(f"\nCapturing {args.seconds}s scan + {args.promisc}s monitor ...")
    for t in threads: t.start()
    for t in threads: t.join()

    for k in ("pico_error", "esp_error"):
        if k in res:
            print(f"  {k}: {res[k]}")

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = {"tool": "rfnames", "label": args.label or f"capture {stamp}",
               "captured_at": stamp, "seconds": args.seconds,
               "wifi": res.get("wifi", []), "ble": res.get("ble", []),
               "promisc": res.get("promisc", []),
               "promisc_label": f"{args.label or 'this capture'}, {stamp}"}

    CAPDIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out = pathlib.Path(args.out)
    else:
        n = len(list(CAPDIR.glob("names_*.json"))) + 1
        out = CAPDIR / f"names_{n:02d}_{stamp[:10]}.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    m = res.get("mean")
    print(f"\n  nRF24     : {res.get('sweeps', 0)} sweeps")
    if m:
        print(f"      2415-2429 {sum(m[15:30])/15:5.1f}%   "
              f"2458-2466 {sum(m[58:67])/9:5.1f}%   2452-2472 {sum(m[52:73])/21:5.1f}%")
    print(f"  Wi-Fi APs : {len(payload['wifi'])}")
    print(f"  BLE       : {len(payload['ble'])}"
          f"  ({sum(1 for b in payload['ble'] if b['name'])} named)")
    print(f"  On-air    : {len(payload['promisc'])} transmitters")
    print(f"\nWritten: {out}")
    print("Now run:  python tools/build_report.py")


if __name__ == "__main__":
    main()
