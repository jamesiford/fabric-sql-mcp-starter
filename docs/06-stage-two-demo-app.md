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

## Next

[Stage three](07-stage-three-comparison.md) adds an optional side-by-side
against Fabric's own data agent.
