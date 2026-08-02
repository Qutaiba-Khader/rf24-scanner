#!/usr/bin/env python3
"""
The web tool promises to run offline from a bare file:// double-click, with no
CDN, no fonts, no network of any kind. That promise is easy to break by accident
with one convenient <script src="https://..."> and impossible to notice on a
machine that happens to be online.

This fails the build if anything in the page would reach the network, or if the
page grows a module `import` (which CORS blocks on file:// and would silently
force everyone onto a local server).

    python tools/check_selfcontained.py web/index.html
"""

import re
import sys
from pathlib import Path

BAD = [
    (r"""<\s*script[^>]*\bsrc\s*=""", "external <script src=> - inline it instead"),
    (r"""<\s*link[^>]*\bhref\s*=\s*['"]https?:""", "external stylesheet/link"),
    (r"""<\s*img[^>]*\bsrc\s*=\s*['"]https?:""", "remote image - embed as a data: URI"),
    (r"""@import\s+url\(""", "CSS @import"),
    (r"""\bfetch\s*\(""", "fetch() - the tool must not talk to the network"),
    (r"""\bXMLHttpRequest\b""", "XMLHttpRequest"),
    (r"""\bnew\s+WebSocket\b""", "WebSocket"),
    (r"""\bnavigator\.sendBeacon\b""", "sendBeacon"),
    (r"""^\s*import\s+.*\sfrom\s""", "ES module import - CORS blocks this on file://"),
    (r"""<\s*script[^>]*\btype\s*=\s*['"]module['"]""", "type=module - breaks file://"),
]


def main(argv):
    if len(argv) < 2:
        print("usage: check_selfcontained.py <file.html>", file=sys.stderr)
        return 2

    failed = False
    for arg in argv[1:]:
        path = Path(arg)
        if not path.is_file():
            print(f"FAIL  {path}: not found")
            failed = True
            continue

        lines = path.read_text(encoding="utf-8").splitlines()
        hits = []
        for pattern, why in BAD:
            rx = re.compile(pattern, re.I | re.M)
            for n, line in enumerate(lines, 1):
                if rx.search(line):
                    hits.append((n, why, line.strip()[:88]))

        print(f"{path}: {len(lines)} lines, {len(path.read_bytes())} bytes")
        if hits:
            failed = True
            print(f"  FAIL  {len(hits)} network/module dependency(ies):")
            for n, why, txt in sorted(hits):
                print(f"          {path}:{n}  {why}")
                print(f"            {txt}")
        else:
            print("  OK    fully self-contained - no network, no modules")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
