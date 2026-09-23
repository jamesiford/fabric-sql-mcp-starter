"""Start and supervise Data API builder as a child process.

The demo is one command. DAB is the MCP server, so the app owns its lifetime
rather than asking you to run it in a second terminal and remember to build the
connection string by hand.

DAB reads its connection string from MSSQL_CONNECTION_STRING. That is assembled
here from the same SQL_SERVER / SQL_DATABASE the rest of the repo uses, so there
is one place to configure the database.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_CONFIG = "dab/dab-config.json"
MCP_URL = os.environ.get("DAB_MCP_URL", "http://localhost:5000/mcp")
STARTUP_TIMEOUT_S = float(os.environ.get("DAB_STARTUP_TIMEOUT_S", "90"))


def connection_string() -> str:
    """Build MSSQL_CONNECTION_STRING from the repo's existing settings."""
    if os.environ.get("MSSQL_CONNECTION_STRING"):
        return os.environ["MSSQL_CONNECTION_STRING"]
    server = os.environ.get("SQL_SERVER", "").strip()
    database = os.environ.get("SQL_DATABASE", "").strip()
    if not server or not database:
        raise RuntimeError(
            "SQL_SERVER and SQL_DATABASE must be set (see .env.example)"
        )
    return (
        f"Server={server},1433;Database={database};"
        "Authentication=Active Directory Default;"
        "Encrypt=Yes;TrustServerCertificate=No;"
    )


def _responding() -> bool:
    """True once the MCP endpoint answers at all.

    A bare GET on /mcp is not a valid MCP request, so DAB replies 4xx. That is
    still proof the host is up and routing, which is all we need here.
    """
    try:
        urllib.request.urlopen(MCP_URL, timeout=3)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001
        return False


class DabProcess:
    def __init__(self, config: str = DEFAULT_CONFIG) -> None:
        self.config = config
        self.proc: subprocess.Popen | None = None
        self.adopted = False

    def start(self) -> None:
        if _responding():
            # Someone is already serving /mcp - most often `dab start` in another
            # terminal. Use it rather than fighting over the port.
            self.adopted = True
            return

        exe = shutil.which("dab")
        if exe is None:
            raise RuntimeError(
                "The 'dab' CLI is not on PATH. Install it with:\n"
                "    dotnet tool install -g Microsoft.DataApiBuilder"
            )

        env = {**os.environ, "MSSQL_CONNECTION_STRING": connection_string()}
        self.proc = subprocess.Popen(
            [exe, "start", "-c", self.config],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + STARTUP_TIMEOUT_S
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"DAB exited during startup (code {self.proc.returncode}). "
                    f"Run `dab start -c {self.config}` directly to see why."
                )
            if _responding():
                return
            time.sleep(1)

        self.stop()
        raise RuntimeError(
            f"DAB did not answer on {MCP_URL} within {STARTUP_TIMEOUT_S:.0f}s."
        )

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    dab = DabProcess(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG)
    dab.start()
    print(f"DAB responding on {MCP_URL} ({'adopted' if dab.adopted else 'started'})")
