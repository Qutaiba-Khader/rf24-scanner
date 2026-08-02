#!/usr/bin/env python3
"""
Name a MAC address once, and have every report name it from then on.

    # paste one Home Assistant bluetooth advertisement (or a JSON list of them)
    python tools/known_devices.py --from-ha ha.json
    python tools/known_devices.py --add 68:FC:CA:B4:43:B7 "55 inch OLED" --note "Samsung TV"
    python tools/known_devices.py --list

WHY THIS EXISTS
---------------
Neither radio can read a friendly name off the air for most devices. The nRF24
sees only energy. The ESP32 sees a name only when the device chooses to
advertise one - and the great majority do not, which is why so many rows say
"(unnamed BLE device)".

Home Assistant already knows these names, because the user gave them. Pasting
one advertisement here is enough to label that MAC in every future report.

WHAT A NAME HERE DOES AND DOES NOT MEAN
---------------------------------------
It is a LABEL, not a measurement. Naming a MAC says nothing about whether that
device is loud enough to matter - the -64 dBm floor still decides that, and the
reports keep showing the signal level next to the name for exactly that reason.

Stored at Desktop/rf24-captures/known_devices.json.
"""

import argparse
import json
import os
import pathlib
import sys

CAPDIR = pathlib.Path(os.path.expanduser("~")) / "OneDrive" / "Desktop" / "rf24-captures"
STORE = CAPDIR / "known_devices.json"

# Bluetooth SIG company identifiers seen in manufacturer_data. Only the few this
# project has actually met - guessing beyond that would invent facts.
COMPANY = {
    0x0075: "Samsung Electronics",
    0x004C: "Apple",
    0x0006: "Microsoft",
    0x00E0: "Google",
    0x0499: "Ruuvi",
    0x02E5: "Espressif",
}


def norm(mac):
    return mac.replace(":", "").replace("-", "").lower()


def load():
    if STORE.is_file():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            print(f"warning: {STORE.name} is unreadable, starting fresh")
    return {}


def save(db):
    CAPDIR.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(db, indent=1, sort_keys=True), encoding="utf-8")


def from_ha(entry, db):
    """One Home Assistant bluetooth advertisement -> one named device."""
    mac = norm(entry.get("address", ""))
    if len(mac) != 12:
        return None
    md = entry.get("manufacturer_data") or {}
    vendors = []
    for k in md:
        try:
            cid = int(k)
        except (TypeError, ValueError):
            continue
        # The key IS the SIG company identifier, so it names the maker even when
        # the OUI is a randomised address that names nobody.
        vendors.append(COMPANY.get(cid, f"SIG company 0x{cid:04X}"))
    rec = {
        "name": entry.get("name") or "",
        "vendor": ", ".join(vendors),
        "source": "home assistant",
        "rssi_at_ha": entry.get("rssi"),
        "seen_by": entry.get("source", ""),
        "connectable": entry.get("connectable"),
    }
    db[mac] = {k: v for k, v in rec.items() if v not in ("", None)}
    return mac


def main():
    ap = argparse.ArgumentParser(description="Map MAC addresses to friendly names.")
    ap.add_argument("--from-ha", metavar="FILE",
                    help="JSON file: one HA advertisement, or a list of them")
    ap.add_argument("--add", nargs=2, metavar=("MAC", "NAME"))
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    db = load()

    if args.from_ha:
        raw = json.loads(pathlib.Path(args.from_ha).read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        added = [m for m in (from_ha(e, db) for e in items) if m]
        save(db)
        for m in added:
            print(f"  named {':'.join(m[i:i+2] for i in range(0,12,2))} -> "
                  f"{db[m].get('name','?')}  ({db[m].get('vendor','')})")
        print(f"\n{len(added)} device(s) added. {len(db)} known in total.")
        return

    if args.add:
        mac = norm(args.add[0])
        if len(mac) != 12:
            sys.exit("MAC must be 12 hex digits")
        db[mac] = {"name": args.add[1], "source": "manual"}
        if args.note:
            db[mac]["vendor"] = args.note
        save(db)
        print(f"named {args.add[0]} -> {args.add[1]}")
        return

    if not db:
        print("Nothing known yet. Add one with --from-ha or --add.")
        return
    print(f"{len(db)} known device(s):\n")
    for mac, rec in sorted(db.items(), key=lambda x: x[1].get("name", "")):
        pretty = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
        print(f"  {pretty}  {rec.get('name','(no name)'):<26}"
              f"{rec.get('vendor','')}  [{rec.get('source','')}]")


if __name__ == "__main__":
    main()
