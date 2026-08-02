#!/usr/bin/env python3
"""
Guard against the bug class that actually bit this project during development.

`$('btnExpCmp')` was wired up while the markup said `id="btnExportCmp"`. Because
that assignment sits at the top level of a classic script, the resulting
TypeError aborted the rest of the script body — so a single typo silently killed
every feature initialised after it. It looked like ten unrelated dead buttons.

This checks that every element id the JavaScript reaches for actually exists in
the HTML, and (informationally) reports ids in the HTML nothing reaches for.

    python tools/check_ids.py web/index.html
"""

import re
import sys
from pathlib import Path

# $('foo')  /  document.getElementById('foo')  /  getElementById("foo")
REF_RE = re.compile(r"""(?:\$\(|getElementById\(\s*)['"]([A-Za-z][\w-]*)['"]\s*\)""")
ID_RE = re.compile(r"""\bid\s*=\s*['"]([^'"]+)['"]""")

# Ids only ever produced at runtime (built into innerHTML), not present statically.
DYNAMIC_OK = set()


def main(argv):
    if len(argv) < 2:
        print("usage: check_ids.py <file.html> [more.html ...]", file=sys.stderr)
        return 2

    failed = False
    for arg in argv[1:]:
        path = Path(arg)
        if not path.is_file():
            print(f"FAIL  {path}: not found")
            failed = True
            continue

        src = path.read_text(encoding="utf-8")

        # Only look for id= inside markup, not inside the <script> block, so a
        # string like id="x" in generated HTML doesn't count as a real element.
        markup = re.sub(r"<script\b.*?</script>", "", src, flags=re.S | re.I)

        declared = set(ID_RE.findall(markup))
        referenced = set(REF_RE.findall(src))

        missing = sorted(referenced - declared - DYNAMIC_OK)
        unused = sorted(declared - referenced)

        print(f"{path}: {len(declared)} ids declared, {len(referenced)} referenced")

        if missing:
            failed = True
            print(f"  FAIL  {len(missing)} referenced id(s) do not exist in the markup:")
            for m in missing:
                for n, line in enumerate(src.splitlines(), 1):
                    if re.search(rf"""(?:\$\(|getElementById\(\s*)['"]{re.escape(m)}['"]""", line):
                        print(f"          {m}  ->  {path}:{n}")
                        break
        else:
            print("  OK    every referenced id exists")

        if unused:
            # Not a failure: labels, wrappers and CSS hooks legitimately have ids
            # the script never touches.
            print(f"  note  {len(unused)} id(s) never referenced by script: {', '.join(unused)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
