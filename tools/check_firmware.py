#!/usr/bin/env python3
"""
Static checks for the MicroPython firmware, aimed at the two bugs that actually
reached hardware and cost a debugging session each.

1. A function that calls itself. A global find-and-replace of
   `sys.stdout.write(` -> `say(` also rewrote the call inside say()'s own body,
   so every write recursed until the stack blew. ast.parse() is perfectly happy
   with that.

2. A name that is used but never defined. Removing host_writable() left a call
   to it inside emit_frame(); on hardware that is a NameError on the first
   frame, and there is no interpreter here to catch it at import time.

    python tools/check_firmware.py firmware/main.py
"""

import ast
import builtins
import sys
from pathlib import Path

# Names MicroPython provides that CPython's builtins do not.
MICROPYTHON_GLOBALS = {"micropython", "const"}


def collect_module_names(tree):
    names = set(dir(builtins)) | MICROPYTHON_GLOBALS
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                names |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            names |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.For):
            names |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    return names


def local_names(fn):
    names = {a.arg for a in fn.args.args}
    if fn.args.vararg:
        names.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        names.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                names |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.For, ast.comprehension)):
            names |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)          # `except Exception as e`
        elif isinstance(node, ast.Global):
            names |= set(node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            names |= {n.id for n in ast.walk(node.optional_vars) if isinstance(n, ast.Name)}
    return names


def main(argv):
    path = Path(argv[1] if len(argv) > 1 else "firmware/main.py")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    problems = []

    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    for fn in funcs:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == fn.name):
                problems.append(f"{path}:{node.lineno} {fn.name}() calls itself "
                                f"(infinite recursion)")


    # @micropython.native SUSPENDS THE BACKGROUND SCHEDULER for the whole
    # function (docs: reference/speed_python.html). The 126-channel sweep ran
    # inside one, so TinyUSB was starved for every pass and USB died the moment
    # scanning began. It cost nine releases to find. Never again.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                name = ""
                if isinstance(dec, ast.Attribute):
                    name = dec.attr
                elif isinstance(dec, ast.Name):
                    name = dec.id
                if name in ("native", "viper"):
                    problems.append(
                        f"{path}:{node.lineno} {node.name}() uses @micropython.{name}, "
                        f"which suspends the scheduler and starves USB")

    module_names = collect_module_names(tree)
    for fn in funcs:
        known = module_names | local_names(fn)
        for node in ast.walk(fn):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id not in known and not node.id.startswith("__")):
                problems.append(f"{path}:{node.lineno} {fn.name}() uses undefined "
                                f"name '{node.id}'")

    print(f"{path}: {len(src.splitlines())} lines, {len(funcs)} functions")
    if problems:
        for p in sorted(set(problems)):
            print("  FAIL  " + p)
        return 1
    print("  OK    no self-recursion, no undefined names")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
