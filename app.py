"""Stage two - the natural-language demo app.

Run:
    python app.py          then open http://127.0.0.1:8000

One lane always, a second lane optionally.

    MCP lane        Data API builder publishes curated views as MCP tools. The
                    model picks a tool and fills in arguments; DAB turns those
                    into parameterised SQL and runs it.

    data agent      STAGE THREE, OPTIONAL. Appears only when
    lane            FABRIC_WORKSPACE_ID and FABRIC_DATA_AGENT_ID are set. The
                    same question goes to a published Fabric data agent, which
                    plans, generates, executes and summarises internally.

Neither lane contains a hand-written query engine. That is the point: the
difference is where the query comes from, not how much bespoke code sits behind
it.

Every stage is streamed to the browser as it happens, so the trace panel shows
real work rather than a replay.

ROW-LEVEL SECURITY AND THE COMPARISON CONFLICT. A Fabric data agent cannot call
sp_set_session_context, so with the claim-driven policy enabled it sees zero
rows. /api/rls reports the live state and the UI warns, rather than silently
showing an empty lane. See docs/07-stage-three-comparison.md.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from typing import Iterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
load_dotenv(os.path.join(HERE, ".env"))

import data_agent  # noqa: E402
from dab_lane import DabLane  # noqa: E402
from dab_process import DabProcess  # noqa: E402
from data_agent import DataAgentLane  # noqa: E402

_dab = DabProcess(os.path.join(HERE, "dab", "dab-config.json"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    _dab.stop()


app = FastAPI(title="Fabric Lakehouse over MCP", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

_mcp_lane: DabLane | None = None
_agent_lane: DataAgentLane | None = None


def mcp_lane() -> DabLane:
    global _mcp_lane
    if _mcp_lane is None:
        _mcp_lane = DabLane()
    return _mcp_lane


def agent_lane() -> DataAgentLane:
    global _agent_lane
    if _agent_lane is None:
        _agent_lane = DataAgentLane()
    return _agent_lane


class Question(BaseModel):
    question: str


class GuardrailCall(BaseModel):
    tool: str
    args: dict


# Each preset returns a COMPLETE result set - no truncation, no top-N. The
# "rows" value is the expected count for the default seed (8,000 accounts,
# 2,000 customers); it is shown on the button so you can see at a glance
# whether the answer is complete.
#
# If you re-seed at a different scale, or point this at your own data, these
# counts will not match. They are a convenience, not an assertion - the UI
# always renders whatever actually came back.
#
# The first two are raw-row reads of the whole table, which is the proof that
# nothing is silently capped. The phrasing model still sees only a sample of
# them, and says so.
#
# 'segment' is deliberately absent: every account in the seed is 'commercial',
# so grouping by it returns one uninteresting row.
PRESETS = [
    {"label": "Every account", "rows": 8000,
     "question": "Show the complete list of deposit accounts - every row in the book, nothing left out."},
    {"label": "Every CD account", "rows": 1500,
     "question": "Show the complete list of accounts whose product code is CD. Every row, not a sample."},
    {"label": "Balance by branch", "rows": 9,
     "question": "What is the total deposit balance for every branch name? List all of them."},
    {"label": "Branch x product grid", "rows": 45,
     "question": "Show the total deposit balance for every combination of branch name and product code. Include all combinations."},
    {"label": "USD only, by branch", "rows": 7,
     "question": "What is the total deposit balance for each branch name, counting USD accounts only? Show every branch."},
    {"label": "Balance by currency", "rows": 5,
     "question": "What is the total deposit balance in each currency? Show every currency."},
    {"label": "Average rate by product", "rows": 5,
     "question": "What is the average interest rate for each product name? Show all products."},
    {"label": "Book by relationship manager", "rows": 10,
     "question": "What is the total deposit balance for each relationship manager? List every one."},
    {"label": "Balance by industry", "rows": 10,
     "question": "What is the total deposit balance by customer industry? Show all industries."},
    {"label": "Branch x currency", "rows": 43,
     "question": "Show the total deposit balance for every branch name and currency combination."},
    {"label": "Maturity ladder, 12 months", "rows": 9,
     "question": "Which branches have term deposits maturing in the next 365 days, and how much? Show every branch."},
    {"label": "Accounts by branch", "rows": 9,
     "question": "How many deposit accounts does each branch name have? List all branches."},
]

# Hostile tool calls, sent straight to DAB with the model bypassed. The point is
# that DAB rejects them, not that a well-behaved model declines to ask. Each
# one is a different class of attack on the published surface.
GUARDRAILS = [
    {
        "label": "Unpublished column",
        "blurb": "Ask for a column that was never published.",
        "tool": "read_records",
        "args": {"entity": "Deposits", "select": "tax_id"},
    },
    {
        "label": "Unpublished base table",
        "blurb": "Reach past the curated views to the customer table.",
        "tool": "read_records",
        "args": {"entity": "customer"},
    },
    {
        "label": "SQL injection via identifier",
        "blurb": "Smuggle a DROP through a group-by name.",
        "tool": "aggregate_records",
        "args": {
            "entity": "Deposits",
            "function": "sum",
            "field": "balance",
            "groupby": ["branch_name; DROP TABLE customer--"],
        },
    },
    {
        "label": "Unsupported operator",
        "blurb": "Use an operator outside the published filter grammar.",
        "tool": "read_records",
        "args": {"entity": "Deposits", "filter": "branch_name regex '.*'"},
    },
]


def _sse(events: Iterator[dict]) -> StreamingResponse:
    def body() -> Iterator[str]:
        for event in events:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/presets")
def presets() -> dict:
    return {
        "presets": PRESETS,
        "guardrails": GUARDRAILS,
        # The UI hides the compare toggle entirely when stage three is not set
        # up, so an unconfigured install shows no broken control.
        "agent_configured": data_agent.is_configured(),
    }


@app.post("/api/stream")
def stream(q: Question) -> StreamingResponse:
    return _sse(mcp_lane().ask_stream(q.question))


@app.post("/api/stream/agent")
def stream_agent(q: Question) -> StreamingResponse:
    """Stage three: the Fabric data agent lane, over its native MCP endpoint."""
    if not data_agent.is_configured():
        def unavailable() -> Iterator[dict]:
            yield {
                "t": "error",
                "id": "lane",
                "message": (
                    "The data agent lane is not configured. Set FABRIC_WORKSPACE_ID "
                    "and FABRIC_DATA_AGENT_ID in .env, then restart. "
                    "See docs/07-stage-three-comparison.md."
                ),
            }
        return _sse(unavailable())
    return _sse(agent_lane().ask_stream(q.question))


@app.get("/api/rls")
def rls_state() -> JSONResponse:
    """Report whether the fail-closed policy would filter the data agent to zero.

    A data agent cannot set session context, so when the claim-driven predicate
    is live it sees nothing. Surfacing that here stops the UI showing an empty
    lane that looks like a failure when it is actually the security policy doing
    exactly its job.

    Only meaningful once stage three is configured - with one lane there is
    nothing to warn about.
    """
    from db import connect

    if not data_agent.is_configured():
        return JSONResponse({"rls_blocking_agent": False, "agent_configured": False})

    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dbo.vw_deposits")
        visible = int(cur.fetchone()[0])
        cur.close()
        conn.close()
        return JSONResponse({
            "agent_configured": True,
            "rls_blocking_agent": visible == 0,
            "visible_rows": visible,
            "hint": (
                "Row-level security is enabled, so the data agent lane will return "
                "no rows - it cannot present a role claim. Run "
                "sql/04-disable-row-level-security.sql to compare, then re-run 03 "
                "afterwards."
            ) if visible == 0 else None,
        })
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


class SqlLookup(BaseModel):
    entity: str
    since_utc: str


@app.post("/api/sql")
def actual_sql(lookup: SqlLookup) -> JSONResponse:
    """The statement DAB actually ran, read back from Fabric's query history.

    DAB does not return its generated SQL over MCP, and Fabric publishes it to
    query history on a lag. The UI asks for it after the answer has landed so
    the lane is never held open waiting.
    """
    return JSONResponse(mcp_lane().actual_sql(lookup.entity, lookup.since_utc))


@app.post("/api/guardrail")
def guardrail(call: GuardrailCall) -> StreamingResponse:
    return _sse(mcp_lane().guardrail_stream(call.tool, call.args))


@app.post("/api/mcp")
def run_mcp(q: Question) -> JSONResponse:
    """Non-streaming fallback, kept for bench/report scripts."""
    return JSONResponse(mcp_lane().ask(q.question))


@app.post("/api/agent")
def run_agent(q: Question) -> JSONResponse:
    """Non-streaming data agent call. Stage three only."""
    if not data_agent.is_configured():
        return JSONResponse(
            {"error": "The data agent lane is not configured. "
                      "See docs/07-stage-three-comparison.md."},
            status_code=409,
        )
    return JSONResponse(agent_lane().ask(q.question))


@app.post("/api/warm")
def warm() -> dict:
    mcp_lane().warm()
    return {"warmed": True}


if __name__ == "__main__":
    import uvicorn

    # Configurable because 8000 is commonly taken. Without this, the app fails
    # to bind and the browser lands on whatever else is already listening.
    port = int(os.environ.get("PORT", "8000"))

    print("Starting Data API builder ...")
    _dab.start()
    print(f"  DAB {'adopted' if _dab.adopted else 'started'} on "
          f"{os.environ.get('DAB_MCP_URL', 'http://localhost:5000/mcp')}")

    print("Warming the MCP lane ...")
    mcp_lane().warm()

    if data_agent.is_configured():
        print("Warming the data agent lane (stage three) ...")
        agent_lane().warm()
    else:
        print("Data agent lane not configured - the compare toggle is hidden.")
        print("  To enable it, see docs/07-stage-three-comparison.md")

    print(f"Ready -> http://127.0.0.1:{port}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        _dab.stop()
