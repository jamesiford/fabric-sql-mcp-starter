"""One place that knows how to connect to the database.

Every script in this repo goes through here, so there is exactly one connection
string to get right and one place to change if you move to a different SQL
target.

Authentication is Microsoft Entra, always. There is no SQL username or password
path in this repo, by design.

A SUBTLETY WORTH KNOWING, because it costs an hour if you meet it cold:

    Data API builder is .NET and uses Microsoft.Data.SqlClient, which accepts
        Authentication=Active Directory Default
    in the connection string.

    This file is Python and uses pyodbc, which does NOT. ODBC Driver 18 has no
    "default" mode - it offers ActiveDirectoryIntegrated, ActiveDirectoryPassword,
    ActiveDirectoryInteractive, ActiveDirectoryServicePrincipal and
    ActiveDirectoryMsi, none of which chain the way the .NET one does.

    So the two halves of this repo authenticate differently, on purpose:

        DAB      connection string keyword     (see src/dab_process.py)
        Python   an Entra access token passed
                 through SQL_COPT_SS_ACCESS_TOKEN   (below)

    Both resolve to the same identity. Only the plumbing differs.
"""

from __future__ import annotations

import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential

# ODBC Driver 18 ships stricter TLS defaults than 17 and is the version
# Microsoft documents for Fabric. Pinning it avoids failing against a machine
# that happens to have an older driver installed.
DRIVER = os.environ.get("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")

# The ODBC attribute that carries a pre-fetched access token. Not exposed as a
# pyodbc constant, so it is spelled out here.
SQL_COPT_SS_ACCESS_TOKEN = 1256

# Scope for Azure SQL and every Fabric SQL surface.
SQL_SCOPE = "https://database.windows.net/.default"

_credential: DefaultAzureCredential | None = None


def _token_struct() -> bytes:
    """Fetch an Entra token and pack it the way ODBC expects.

    The driver wants a 4-byte little-endian length followed by the token as
    UTF-16-LE. Getting this wrong produces a login failure with no useful
    message, so it is isolated here.
    """
    global _credential
    if _credential is None:
        # DefaultAzureCredential chains the sensible options in order: an
        # environment service principal, a managed identity when running in
        # Azure, then the local `az login` session. One code path for laptop
        # and cloud alike.
        _credential = DefaultAzureCredential()

    token = _credential.get_token(SQL_SCOPE).token
    encoded = token.encode("utf-16-le")
    return struct.pack("<i", len(encoded)) + encoded


def connection_string() -> str:
    """Build the ODBC connection string from .env.

    Note the absence of an Authentication= keyword: the token supplied
    separately in connect() is what authenticates.
    """
    server = os.environ.get("SQL_SERVER", "").strip()
    database = os.environ.get("SQL_DATABASE", "").strip()
    if not server or not database:
        raise RuntimeError(
            "SQL_SERVER and SQL_DATABASE must be set. Copy .env.example to .env "
            "and fill them in - see the README."
        )
    # Tolerate a port pasted in with the server name; it is added below.
    server = server.split(",")[0].strip()
    return (
        f"Driver={{{DRIVER}}};"
        f"Server={server},1433;"
        f"Database={database};"
        "Encrypt=Yes;"
        "TrustServerCertificate=No;"
        "Connection Timeout=60;"
    )


def connect() -> pyodbc.Connection:
    """Open a connection, translating the failures people actually hit."""
    try:
        return pyodbc.connect(
            connection_string(),
            attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()},
        )
    except pyodbc.InterfaceError as exc:
        text = str(exc)
        if "IM002" in text or "not found" in text.lower():
            raise RuntimeError(
                f"ODBC driver not found. Install '{DRIVER}':\n"
                "  https://learn.microsoft.com/sql/connect/odbc/"
                "download-odbc-driver-for-sql-server"
            ) from exc
        raise
    except pyodbc.Error as exc:
        text = str(exc)
        if "Login failed" in text or "AADSTS" in text:
            raise RuntimeError(
                "Entra sign-in succeeded but the database rejected the identity.\n"
                "  - run `az login`, and check `az account show` is the right tenant\n"
                "  - confirm your account has access to this database\n"
                "See docs/05-troubleshooting.md."
            ) from exc
        if "Cannot open server" in text or "Cannot open database" in text:
            raise RuntimeError(
                f"Could not open '{os.environ.get('SQL_DATABASE')}' on "
                f"'{os.environ.get('SQL_SERVER')}'.\n\n"
                "  For a Fabric SQL database the database name is NOT just the\n"
                "  item name - the item GUID is appended, for example:\n"
                "      deposits-aef098c2-f1e6-4bdd-b132-e8430f86b32f\n"
                "  Copy it from Settings -> Connection strings."
            ) from exc
        raise


def run_sql_file(path: str) -> None:
    """Execute a .sql file, splitting on GO like a SQL tool would.

    pyodbc has no notion of GO - it is a client batch separator, not T-SQL - so
    a file containing CREATE VIEW or CREATE FUNCTION must be split before
    execution. Each batch runs in its own execute() call.
    """
    with open(path, encoding="utf-8") as handle:
        content = handle.read()

    batches: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip().upper() == "GO":
            if current:
                batches.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        batches.append("\n".join(current))

    conn = connect()
    conn.autocommit = True
    cursor = conn.cursor()
    try:
        for batch in batches:
            if not batch.strip():
                continue
            cursor.execute(batch)
            # PRINT output arrives as driver messages; surface it so the SQL
            # scripts can report progress to whoever is running them.
            for message in cursor.messages or []:
                text = message[1]
                if "]" in text:
                    text = text.split("]")[-1].strip()
                if text:
                    print(f"  {text}")
            while cursor.nextset():
                pass
    finally:
        cursor.close()
        conn.close()
