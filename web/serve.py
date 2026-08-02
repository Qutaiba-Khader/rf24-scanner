#!/usr/bin/env python3
"""
Fallback launcher for the rf24scan web tool.

You normally do NOT need this. Chrome treats file:// as a secure context, so
double-clicking index.html is enough for the Web Serial API to work.

Use this only if Connect is refused with a "secure context" complaint, or if
you want to open the tool from another machine on your LAN.

    python serve.py            # http://localhost:8000, opens your browser
    python serve.py 9000       # different port
    python serve.py --lan      # also reachable from other machines

Nothing is uploaded anywhere. This serves one static file from this folder.
"""

import http.server
import os
import socket
import socketserver
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def end_headers(self):
        # The tool is developed live; never let a stale copy be cached.
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    args = [a for a in sys.argv[1:]]
    lan = "--lan" in args
    if lan:
        args.remove("--lan")
    port = int(args[0]) if args else 8000
    host = "0.0.0.0" if lan else "127.0.0.1"

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((host, port), Handler) as httpd:
        url = "http://localhost:%d/index.html" % port
        print("rf24scan  ->  %s" % url)
        if lan:
            print("LAN       ->  http://%s:%d/index.html" % (lan_ip(), port))
            print("NOTE: Web Serial needs a secure context. Plain http:// works for")
            print("      localhost only - from another machine the charts, demo mode")
            print("      and session loading work, but Connect will not.")
        print("Ctrl-C to stop.\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
