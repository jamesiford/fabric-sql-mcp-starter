# Stage two — the natural-language demo app

> **Included in this repo.** Activates when you set the environment variables
> below. Without them the feature stays hidden and nothing else changes.

---

## What stage one already gives you

Before assuming you need this, check whether you do. With stage one deployed you
can already ask natural-language questions through:

| Client | Effort |
| --- | --- |
| **VS Code agent mode** | `.vscode/mcp.json` is already wired — open the folder, start the server, ask |
| **Copilot Studio** | Add the MCP server as a tool. Low-code, and this is the route to Teams |
| **Microsoft Foundry** | Add it as an MCP server knowledge source |
| **Any MCP client** | Point it at the endpoint |

In all four cases the model is provided by the client. You do not need to deploy
one, and you do not need this app.

**Stage two exists for one reason:** you want a self-contained web page you
control — to demo without depending on someone else's client, to embed in an
internal portal, or to show the full request trace to an audience.

---

## What it adds

A small FastAPI application that performs the three-step turn:

```
question ──> model picks a tool ──> DAB runs the SQL ──> model writes the answer
             (~2.5 s)                (~0.3 s)             (~1.5 s, streamed)
```

Plus a browser UI showing, live:

- which tool the model chose and with what arguments
- the row count that came back
- the answer streaming token by token
- a timing breakdown of each stage

That trace panel is the point. It makes "the model never writes SQL" something
an audience watches happen rather than something they are told.

---

## What it costs

| | |
| --- | --- |
| **Azure OpenAI deployment** | One chat model. `gpt-4.1-mini` or similar is ample. |
| **Cost** | Two calls per question. Pennies for a demo. |
| **Extra code** | ~120 lines of orchestration. Not an agent framework. |
| **Extra config** | Two environment variables. |

The orchestration is deliberately small. It calls the model, passes the chosen
tool to DAB, and calls the model again. It holds no query logic — the tool
schemas handed to the model are exactly what DAB advertises over `tools/list`,
used verbatim, so changing `dab-config.json` changes the model's options with no
code edit.

---

## When it is ready

Add to `.env`:

```ini
AZURE_OPENAI_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
```

Uncomment the stage-two block in `requirements.txt`, then:

```bash
pip install -r requirements.txt
python app.py
```

If the two variables are absent, the app will say so rather than failing
obscurely.

---

## The first thing that will look broken

Ask a question and every answer comes back empty — "the total cannot be
determined from these rows", or a single row of nulls.

Nothing is broken. `setup.py` applies row-level security by default, that policy
is **claim-driven and fail-closed**, and a local session presents no claim. The
database is correctly showing you nothing. The 8,000 accounts are still there.

The app detects this on load and shows a banner explaining it. To see data:

```bash
python scripts/rls.py --off
```

Put it back when you are done demonstrating:

```bash
python scripts/rls.py --on
```

Run it with no arguments to report the current state.

This is a real property of the design rather than a rough edge: the filter sits
in the database, below the API, so *nothing* that fails to present an identity
can read the data — including your own demo app. That is the point. See
[row-level security](03-row-level-security.md).

## The "SQL DAB actually ran" panel says pending

The trace panel recovers DAB's generated statement from the database's own query
history. On Fabric SQL Database that is Query Store, which ships with
`QUERY_CAPTURE_MODE = AUTO` — and AUTO deliberately skips queries that are cheap
or infrequent, which is exactly what these are. The statement never gets
recorded, so the panel stays on `pending`.

Turn it on once per database:

```sql
ALTER DATABASE CURRENT SET QUERY_STORE = ON;
ALTER DATABASE CURRENT SET QUERY_STORE (
    QUERY_CAPTURE_MODE = ALL,
    DATA_FLUSH_INTERVAL_SECONDS = 60
);
```

Even then the statement appears on Query Store's flush interval, not instantly —
allow up to a minute. Everything else on the page is unaffected; this panel is a
teaching aid, and the answer above it never depends on it.

---

## Next

[Stage three](07-stage-three-comparison.md) adds an optional side-by-side
against Fabric's own data agent.
