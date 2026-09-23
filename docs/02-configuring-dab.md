# Configuring `dab-config.json`

This is the file that decides what an AI agent can and cannot reach in your
database. It is worth understanding line by line, because **everything the model
knows about your data comes from here**, and so does everything it is prevented
from doing.

The config in this repo works as-is against the sample deposit book. This
document explains why each part is the way it is, so you can point it at your
own schema with confidence.

---

## The one idea to take away

> **The descriptions are the interface.**

A model never sees your physical schema. It sees the entity names, the field
names, and the `description` strings you write here — and nothing else. A column
you do not publish cannot be selected, filtered, grouped or aggregated, no
matter what the model is asked.

That cuts both ways:

- A vague description produces a confused agent that writes plausible-looking
  nonsense.
- A precise description produces an agent that asks the right question, or
  correctly declines.

If you only invest effort in one part of this file, invest it in the
descriptions.

---

## 1. The data source

```json
"data-source": {
  "database-type": "mssql",
  "connection-string": "@env('MSSQL_CONNECTION_STRING')",
  "options": {
    "set-session-context": true
  }
}
```

| Setting | Why it is what it is |
| --- | --- |
| `database-type` | `mssql` covers the whole SQL Server family: **Fabric SQL Database**, Azure SQL, SQL Managed Instance, and SQL Server itself. Use it for all of them. |
| `connection-string` | `@env(...)` reads an environment variable, so no credential is ever written into this file, and the same file works locally and in Azure. |
| `set-session-context` | **This one line enables per-user security.** Off by default. |

### What `set-session-context` actually does

With it enabled, before every query Data API builder emits:

```sql
EXEC sp_set_session_context
    'http://schemas.microsoft.com/ws/2008/06/identity/claims/role',
    @session_param0;
```

That carries the caller's claims, from their validated token, into the SQL
session. A row-level security predicate can then read them back with
`SESSION_CONTEXT()` and filter rows accordingly — see
[`sql/03-row-level-security.sql`](../sql/03-row-level-security.sql).

The filter is applied **by the database, below the API**. No caller — not the
model, not the MCP client, not a misconfigured orchestrator — can bypass it.

> **A note if you are also using a Fabric Lakehouse.** The documentation
> suggests `dwsql` for Fabric endpoints. In testing against a Lakehouse SQL
> analytics endpoint, `dwsql` broke aggregation with `Invalid column name`
> errors. `mssql` worked correctly. This is why the primary path in this repo is
> Fabric SQL Database, where `mssql` is unambiguously correct.

---

## 2. The runtime — turning surfaces off

```json
"rest":    { "enabled": false },
"graphql": { "enabled": false }
```

Data API builder can expose REST and GraphQL as well as MCP. This repo turns
both off so that **MCP is the only way in**. One surface is one surface to
secure, log and reason about.

Turn REST back on if you want it — just do so deliberately.

### Pagination

```json
"pagination": {
  "max-page-size": 100000,
  "default-page-size": 100000
}
```

The defaults are much smaller. They are raised here for a specific reason: if a
user asks *"show the total for every branch"* and the server quietly returns the
first 100, the answer is **wrong in a way nobody can see**. Truncation the
caller cannot detect is a correctness bug, not a performance optimisation.

Raise or lower this to suit your data, but decide consciously. If you lower it,
say so in the entity description so the model can warn the user.

---

## 3. The MCP block — the tool surface

```json
"mcp": {
  "enabled": true,
  "path": "/mcp",
  "description": "Read-only analytics over a commercial deposit book ...",
  "dml-tools": {
    "describe-entities": true,
    "read-records": true,
    "aggregate-records": { "enabled": true, "query-timeout": 30 },
    "create-record": false,
    "update-record": false,
    "delete-record": false,
    "execute-entity": false
  }
}
```

### The server description

This text is advertised to every client that connects. An orchestrator uses it
to decide whether this server is relevant to a question at all. Write it as if
explaining the server to a competent colleague who has never seen your data.

### `dml-tools` — the write surface

The four `false` entries are the most important lines in the file.

A disabled tool is **not advertised in `tools/list`**. A caller cannot discover
it, so cannot call it. This is meaningfully stronger than a permission check
that rejects a request after it arrives — the capability is absent, not denied.

After starting the server, verify for yourself:

```bash
python scripts/inspect_server.py
```

Expect exactly three tools. If you ever see `create_record` in that list,
something is wrong with your config.

| Tool | What it does |
| --- | --- |
| `describe_entities` | Returns entity names, fields and descriptions. The model's entire map. |
| `read_records` | Row-level reads with `select`, `filter`, `orderby`, paging. |
| `aggregate_records` | `count`, `avg`, `sum`, `min`, `max` with `groupby`, `having`, `filter`. |

### `query-timeout`

Set on `aggregate-records` because aggregation is where a careless `groupby` can
become expensive. Thirty seconds is generous for the sample data; tune it to
your own.

---

## 4. Host and authentication — read this part twice

```json
"host": {
  "mode": "production",
  "cors": { "origins": [], "allow-credentials": false },
  "authentication": { "provider": "Unauthenticated" }
}
```

### ⚠️ `"provider": "Unauthenticated"` is for local development only

On `localhost`, this is fine — only you can reach the port.

**On a public Azure Container Apps ingress, it means anyone who discovers the
URL can read your database.** There is no second line of defence. This is the
single most likely serious mistake when moving from laptop to cloud.

Before you expose the server publicly, change it to Entra ID:

```json
"authentication": {
  "provider": "AzureAD",
  "jwt": {
    "audience": "<your-app-registration-client-id>",
    "issuer": "https://login.microsoftonline.com/<your-tenant-id>/v2.0"
  }
}
```

Then replace the `anonymous` role on every entity:

```json
"permissions": [
  { "role": "RelationshipManager", "actions": [ { "action": "read" } ] },
  { "role": "svc-all",             "actions": [ { "action": "read" } ] }
]
```

Now only a caller presenting a validated token carrying one of those roles can
read anything — and the role claim is exactly what the row-level security
predicate filters on, so authentication and entitlement line up.

Full walkthrough: [`docs/04-deploy-to-azure.md`](04-deploy-to-azure.md).

### `mode: production`

Suppresses detailed error messages in responses. Use `development` locally if
you want verbose errors while iterating; never in production, because those
errors describe your schema.

### CORS

An empty `origins` array means no browser origin is allowed. Add yours only if a
browser calls the server directly.

---

## 5. Entities — pointing this at your own data

This is the part you will actually rewrite. Each entity maps one database object
to one named thing the model can reach.

```json
"Deposits": {
  "description": "One row per deposit account, pre-joined to its customer ...",
  "source": {
    "object": "dbo.vw_deposits",
    "type": "view",
    "key-fields": [ "account_number" ]
  },
  "rest":    { "enabled": false },
  "graphql": { "enabled": false },
  "fields": [ ... ],
  "permissions": [
    { "role": "anonymous", "actions": [ { "action": "read" } ] }
  ]
}
```

### Publish views, not tables

Every entity here points at a **view**. That is deliberate, and it is Microsoft's
own recommendation for SQL MCP Server wherever joins or computed logic are
involved.

Three reasons it matters:

1. **The view is the security boundary.** A column you do not project cannot be
   reached. If `tax_id` is not in the view, no configuration mistake further up
   the stack can expose it.
2. **Joins are resolved once, correctly.** The model never has to infer how
   `customer` relates to `deposit_account`, so it can never get it wrong.
3. **The logic lives where your data team can review it.** A view is versioned,
   reviewable SQL — not application code the data team never sees.

Compare the two maturity questions:

| Approach | What the model must do |
| --- | --- |
| Raw table | Compute `DATEDIFF` against a snapshot date it has to infer |
| `vw_maturity_ladder` | Filter `days_to_maturity le 90` |

The second is nearly impossible to get wrong. That is modelling work done once,
in SQL, paying off on every question thereafter.

### `key-fields` is required for views

A table has a primary key; a view does not. `key-fields` tells DAB what makes a
row unique. `CustomerExposure` needs three, because its grain is
customer × currency × product:

```json
"key-fields": [ "customer_id", "currency", "product_code" ]
```

Get this wrong and paging misbehaves in ways that are hard to diagnose.

### Writing field descriptions

Compare:

```json
{ "name": "currency", "description": "The currency." }
```

against what this repo ships:

```json
{ "name": "currency",
  "description": "ISO currency code of the account balance. One of: USD, CNY, HKD, JPY, EUR. Shanghai and Hong Kong Central hold no USD accounts." }
```

The second tells the model three things it cannot discover on its own: the
format, the permitted values, and a real distribution quirk. That last clause is
what stops it inventing a USD row for Shanghai.

Good descriptions state:

- **Format and units** — `"as a percentage, e.g. 3.907 means 3.907%"`
- **Permitted values** — `"One of: CD, DDA, NOW, MMA, FX"`
- **Cardinality** — `"Ten distinct values"` tells the model roughly what a
  grouped result should look like
- **Traps** — `"Null for everything except CD"`
- **Where to go instead** — `"use the MaturityLadder entity for maturity questions"`

### A worked example: your own schema

Say you have `dbo.loan_account` and want to publish it.

**Step 1 — write a view, not a table reference.** Pre-join the dimensions a
question would need, and leave out anything sensitive:

```sql
CREATE VIEW dbo.vw_loans
WITH SCHEMABINDING
AS
SELECT l.loan_number, l.principal, l.rate, l.originated_on, l.matures_on,
       c.customer_name, c.relationship_manager,
       b.branch_name
FROM dbo.loan_account AS l
INNER JOIN dbo.customer AS c ON c.customer_id = l.customer_id
INNER JOIN dbo.branch   AS b ON b.branch_code = l.branch_code;
```

**Step 2 — add the entity.** Either edit the JSON, or use the CLI:

```bash
dab add Loans \
  --source dbo.vw_loans \
  --source.type view \
  --source.key-fields loan_number \
  --permissions "anonymous:read" \
  --config dab/dab-config.json
```

**Step 3 — describe the entity and every field.** This is the real work, and
where answer quality is decided.

**Step 4 — restart and confirm what the model now sees:**

```bash
python scripts/inspect_server.py
```

`describe_entities` should return your new entity with its descriptions. If a
description is missing, the model is guessing about that field.

---

## 6. Telemetry

```json
"telemetry": {
  "open-telemetry": {
    "enabled": false,
    "endpoint": "@env('OTEL_EXPORTER_OTLP_ENDPOINT')",
    "service-name": "deposits-mcp"
  }
}
```

Off by default so the repo runs with no extra infrastructure. Set `enabled` to
`true` and point it at a collector to get distributed traces for every tool
call, with no instrumentation code — useful when someone asks *"which agent read
what, and when?"*

---

## Checklist before going to production

- [ ] `authentication.provider` is **not** `Unauthenticated`
- [ ] Entity permissions use named roles, not `anonymous`
- [ ] Every entity points at a view, not a base table
- [ ] Every published field has a real description
- [ ] `key-fields` set correctly on every view-backed entity
- [ ] `create/update/delete/execute` all `false`, verified in `tools/list`
- [ ] Row-level security enabled if different users should see different rows
- [ ] `mode` is `production`
- [ ] CORS origins are explicit, or empty
- [ ] Telemetry pointed at a collector you actually read

---

## Reference

- [SQL MCP Server documentation hub](https://learn.microsoft.com/sql/mcp/)
- [Data API builder configuration reference](https://learn.microsoft.com/azure/data-api-builder/configuration-file)
- [Authorization and roles](https://learn.microsoft.com/azure/data-api-builder/concept/security/authorization)
- [Add descriptions to entities](https://learn.microsoft.com/azure/data-api-builder/mcp/how-to-add-descriptions)
