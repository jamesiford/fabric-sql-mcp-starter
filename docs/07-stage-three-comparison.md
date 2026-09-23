# Stage three — comparing against a Fabric data agent

> **Included in this repo.** Requires [stage two](06-stage-two-demo-app.md).
> Activates when FABRIC_WORKSPACE_ID and FABRIC_DATA_AGENT_ID are set; without
> them the compare toggle stays hidden.

---

## What it adds

A second lane in the demo app. The same question goes to both:

- **this MCP server** — model picks a tool, DAB runs fixed SQL, model phrases
- **a Fabric data agent** — Fabric plans, generates, executes and summarises
  internally

Both read the same database. Both return the same numbers. What differs is
latency, transparency, and what you can enforce.

When `FABRIC_DATA_AGENT_ID` is not set, the compare toggle does not appear. The
app works exactly as it does in stage two. Nothing to uninstall.

---

## ⚠️ Read this before trying it

**A Fabric data agent cannot participate in row-level security.**

It has no mechanism to call `sp_set_session_context`, so it cannot carry a
caller's identity into the SQL session. Against the fail-closed policy in
`sql/03-row-level-security.sql` it sees **zero rows** and answers:

> *"the query returned no data for the deposit view."*

That is the security policy working correctly. But it means:

| You want to show | Do this |
| --- | --- |
| Per-user entitlement | Run `sql/03-row-level-security.sql` |
| The data agent comparison | Run `sql/04-disable-row-level-security.sql` |

**Not both at once.** Flip between them by re-running the relevant script; no
data is affected either way.

This is not a limitation of the comparison — it *is* one of the findings. Per-user
entitlement enforced at the database is reachable on one path and not the other.

---

## What you need

| | |
| --- | --- |
| A Fabric capacity | F2 or higher, or Power BI Premium P1+ |
| A published data agent | Over the same data. It must be **published** — the MCP endpoint does not exist for a draft |
| Tenant settings | Cross-geo processing and storing for AI enabled |

### Creating the agent

1. In your Fabric workspace: **+ New item → Data agent**
2. Add your data source and select the schema, table and columns it may see
3. Write the agent instructions — this is where domain semantics live, as prose
   rather than as SQL
4. Write the data-source instructions — which object to prefer, which filters to
   always apply
5. **Publish**, supplying a description. That description becomes the MCP tool
   description, so it decides when an orchestrator calls the agent at all

Then set in `.env`:

```ini
FABRIC_WORKSPACE_ID=<from the workspace URL>
FABRIC_DATA_AGENT_ID=<from the agent URL>
```

The endpoint is constructed as:

```
https://api.fabric.microsoft.com/v1/mcp/workspaces/{ws}/dataagents/{id}/agent
```

---

## What the comparison actually shows

Measured against a synthetic book of ~8,000 accounts on an F2 capacity, five
questions, three repeats each:

| | Fabric data agent | This MCP server |
| --- | --- | --- |
| Time per question | ~19–27 s end to end | ~0.3 s query execution |
| Tools advertised | 1, opaque (`userQuestion` in, prose out) | 3, each with a typed schema |
| SQL visible to the caller | no | yes |
| Row count visible | no | yes |
| Rows returned | no — prose only | yes |
| Model choice | none | yours |
| Streaming | no | yes |
| Per-user RLS | **not possible** | proven |
| Infrastructure to run | none | a container you host |
| Code to write | none | ~120 lines |

The last two rows are real advantages of the data agent, and should be said out
loud. If every caller may see all the data and prose is all you need, it is
materially less to build and less to operate.

The comparison is not "which is better." It is "which constraint decides this."

---

## Reading the result honestly

Three caveats worth stating if you show this to anyone:

1. **Check which runtime your agent uses.** Fabric offers a standard and a
   preview runtime, and the preview includes latency improvements. Benchmarking
   against the slower one and presenting the gap as inherent would not be fair.
   The runtime is fixed at publish time.

2. **Capacity size matters.** These numbers are from an F2, the smallest SKU
   sold. A larger capacity helps the data agent. The architectural difference —
   generating a query versus filling a pre-approved one — does not disappear,
   but the multiple is not a constant.

3. **The MCP number is query execution; the agent number is end to end.** The
   MCP lane also pays model latency to route and phrase, bringing its full turn
   to roughly 4–8 s. The honest claim is that *data access* drops from tens of
   seconds to hundreds of milliseconds, and the rest is model latency you
   control.

---

## Reference

- [Consume a data agent as an MCP server](https://learn.microsoft.com/fabric/data-science/data-agent-mcp-server)
- [Fabric data agent concept](https://learn.microsoft.com/fabric/data-science/concept-data-agent)
- [Fabric data agent runtime](https://learn.microsoft.com/fabric/data-science/data-agent-runtime)
- [Row-level security](03-row-level-security.md) — why the two demos conflict
