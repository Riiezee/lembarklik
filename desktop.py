"""Windows desktop entry point for LembarKlik."""

import ctypes
import sys
import threading

from lembarklik.__main__ import create_server


def run_desktop():
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    with create_server() as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        webview.create_window("LembarKlik", url, width=1100, height=760, min_size=(640, 480))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            webview.start(gui="edgechromium", private_mode=True)
        finally:
            server.shutdown()
            worker.join()


def main():
    try:
        run_desktop()
    except Exception as error:
        if sys.platform != "win32":
            raise
        message = (
            "LembarKlik gagal dibuka. Pastikan Microsoft Edge WebView2 Runtime terpasang.\n\n"
            f"{error}"
        )
        ctypes.windll.user32.MessageBoxW(None, message, "LembarKlik", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
