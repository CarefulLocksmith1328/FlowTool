"""Windowless Windows launcher: open the local graphical UI in the default browser."""
from __future__ import annotations

import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

import app


def main():
    app.DATA.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        log = (app.DATA / "flowtool.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
    app.connect().close()
    app.DESKTOP_MODE = True
    stop = threading.Event()
    server = ThreadingHTTPServer((app.HOST, 0), app.Handler)
    url = f"http://{app.HOST}:{server.server_port}/"
    print(f"FlowTool: {url}")
    threading.Thread(target=app.scheduler, args=(stop,), daemon=True).start()
    threading.Timer(0.6, lambda: webbrowser.open_new_tab(url)).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
