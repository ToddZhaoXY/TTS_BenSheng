"""Safe in-place restart for the localhost studio process."""
import os
import sys
import threading

from .engine import ROOT


def restart_process(manager, delay=0.55):
    """Replace the idle server process, preserving its terminal and PID."""
    host = os.environ.get("STUDIO_HOST", "127.0.0.1")
    port = os.environ.get("STUDIO_PORT", "8765")

    def replace():
        manager.shutdown()
        for current in (manager.engine, manager.legacy_engine):
            if current is not None and hasattr(current, "close"):
                current.close()
        os.chdir(ROOT)
        os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "studio.server:create_app",
                                  "--factory", "--host", host, "--port", port])

    threading.Timer(delay, replace).start()
