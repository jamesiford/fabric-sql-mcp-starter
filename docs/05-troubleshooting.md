# Troubleshooting

The errors you are most likely to meet, in roughly the order you might meet
them. Each one includes how to tell it apart from its look-alikes.

---

## Setup and connection

### `Invalid value specified for connection string attribute 'Authentication'`

You are using `Authentication=ActiveDirectoryDefault` with pyodbc. ODBC does not
support it — that is a .NET SqlClient keyword.

This repo already handles it: `src/db.py` fetches an Entra token and passes it
through `SQL_COPT_SS_ACCESS_TOKEN` instead. If you see this error you have
probably written your own connection string; use `db.connect()`.

Note the two halves of this repo authenticate differently on purpose:

| Component | Mechanism |
| --- | --- |
| Data API builder (.NET) | `Authentication=Active Directory Default` in the connection string |
| Python scripts (pyodbc) | an access token passed as a connection attribute |

Both resolve to the same identity.

### `Cannot open database` on a Fabric SQL database

**The database name is not just the item name.** Fabric appends the item GUID:

```
deposits-aef098c2-f1e6-4bdd-b132-e8430f86b32f
```

Putting `deposits` in `.env` will fail. Copy the real value from
**Settings → Connection strings** in the Fabric portal, or from
`properties.databaseName` if you are using the REST API.

This catches nearly everyone once.

### `ODBC driver not found` / `IM002`

`pyodbc` needs Microsoft's ODBC driver, which is a separate install from Python.

Install **[ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)**.

Driver 17 is not sufficient: Fabric requires the TLS defaults that shipped in 18.

Check what you have:

```powershell
Get-OdbcDriver | Where-Object Name -like "*SQL Server*"
```

```bash
odbcinst -q -d
```

If your driver has a different name, set it in `.env`:

```ini
ODBC_DRIVER=ODBC Driver 18 for SQL Server
```

### `Login failed for user '<token-identified principal>'`

Entra authenticated you, but the database does not know who you are.

- Run `az login` and confirm `az account show` is the right tenant.
- Confirm your account has access to the database in Fabric.
- If this is the **deployed container**, you have not run
  [`deploy/grant-managed-identity.sql`](../deploy/grant-managed-identity.sql) —
  the most common post-deployment error.

### `Cannot open server ... requested by the login`

Usually a wrong `SQL_SERVER`.

For a Fabric SQL database, copy it from **Settings → Connection strings**. It
should look like:

```
abcdefghijk-xxxxxxxxxx.database.fabric.microsoft.com
```

Not the workspace name, and not a Lakehouse endpoint.

### `Timeout expired` on the first connect

Usually a paused or cold Fabric capacity. Open the database in the Fabric portal,
wait for it to respond there, then retry.

---

## Seeding

### `Invalid object name 'dbo.customer'`

`sql/01-schema.sql` has not run, or ran against a different database.

```bash
python scripts/setup.py
```

### The seed is slow

Expected: roughly 30–90 seconds for 8,000 accounts, most of it network round
trips rather than SQL.

If it is much slower, you are likely on a paused capacity, or a long way from
the region.

Smaller book while iterating:

```bash
python scripts/seed.py --accounts 500
```

### `String data, right truncation`

A generated value exceeded a column width — only possible if you have edited
either the schema or the generator. Compare column widths in
`sql/01-schema.sql` against the values in `scripts/seed.py`.

---

## Row-level security

### An aggregate returns one row of `null` instead of no rows

For example:

```json
{ "status": "success", "result": [ { "sum_balance": null } ] }
```

This is row-level security, not a broken query. `SUM` over an empty set is
`NULL` in SQL, so a filtered-to-nothing aggregate returns **one row containing
null** rather than zero rows. A grouped query returns zero rows; an ungrouped
aggregate returns one null row.

It looks like a failure and is not. Confirm by presenting a claim:

```sql
EXEC sp_set_session_context
    'http://schemas.microsoft.com/ws/2008/06/identity/claims/role', 'svc-all';
SELECT SUM(balance) FROM dbo.vw_deposits;
```

Through DAB, remember that with `provider: Unauthenticated` no claim is sent at
all, so **every** query is filtered to nothing while the policy is on. For local
development, either disable the policy or configure Entra authentication so a
real claim arrives.

### Everything returns zero rows

**This is usually correct behaviour, not a fault.**

The policy in `sql/03-row-level-security.sql` is fail-closed: a connection that
presents no role claim sees nothing. A plain `SELECT` from SSMS presents no role
claim.

Confirm that is what is happening:

```sql
SELECT name, is_enabled FROM sys.security_policies WHERE name = 'rls_rm_book';
```

To read the data yourself, present the break-glass role first:

```sql
EXEC sp_set_session_context
    'http://schemas.microsoft.com/ws/2008/06/identity/claims/role', 'svc-all';

SELECT COUNT(*) FROM dbo.vw_deposits;
```

Or turn the policy off while you work:

```
sql/04-disable-row-level-security.sql
```

### RLS is on but everyone still sees everything

`set-session-context` is not enabled. In `dab/dab-config.json`:

```json
"options": { "set-session-context": true }
```

Restart the server after changing it — DAB reads config at startup only.

### The data agent comparison returns no data

Expected. A Fabric data agent cannot set session context, so the fail-closed
predicate filters it to zero rows.

You can demonstrate per-user security **or** the data agent comparison, not both
at once. Run `sql/04-disable-row-level-security.sql` before the comparison, and
re-run `03` afterwards. See [`07-stage-three-comparison.md`](07-stage-three-comparison.md).

---

## The MCP server

### `dab: command not found`

```bash
dotnet tool install -g Microsoft.DataApiBuilder
```

If it installs but is not found, your shell needs `~/.dotnet/tools` on `PATH`.
Open a new terminal afterwards.

### The container starts, then every query fails

Read the log first — there are two different failures with similar symptoms:

```bash
az containerapp logs show --name sql-mcp-server --resource-group rg-sql-mcp --tail 60
```

**`Validation of user's permissions failed. Verify the user has the Read item
permission.`**

This is the **Fabric** permission layer, not SQL. The SQL grant has already
worked; the identity still needs access to the Fabric workspace or item. See
[04-deploy-to-azure.md, part 2](04-deploy-to-azure.md#part-2--the-fabric-item-permission).
This step does not exist for Azure SQL, so it surprises people coming from that
background.

**`Login failed for user '<token-identified principal>'`** with no further
detail

The SQL grant has not run, or the user name does not match the container app
name exactly.

**`Login failed ... State:240`** with no permission message

Usually the connection string. In Azure Container Apps use:

```
Authentication=Active Directory Managed Identity
```

not `Active Directory Default`. The Default chain works locally but does not
reliably resolve a system-assigned identity inside a container.

Remember to restart the revision after fixing permissions — Data API builder
reads the schema once at startup and will not retry on its own.

### `/health` returns 403

Expected when `host.mode` is `production` — Data API builder restricts the
health endpoint outside development mode. The MCP endpoint at `/mcp` is
unaffected.

To check a deployment is alive, use `scripts/inspect_server.py` rather than
`curl /health`. It exercises the real protocol, which is a better test anyway.

### The server starts but a tool call fails

Read the terminal where `dab start` is running — DAB logs the failing SQL
statement, which is usually enough on its own.

Most common causes:
- The view does not exist — re-run `sql/02-views.sql`
- The entity's `source.object` name is wrong — it must include the schema
- A field listed in the entity is not in the view

### `tools/list` shows write tools

Your `dml-tools` block is wrong. All four of these must be `false`:

```json
"create-record":  false,
"update-record":  false,
"delete-record":  false,
"execute-entity": false
```

Restart, then verify:

```bash
python scripts/inspect_server.py
```

### A question returns fewer rows than expected

Check `pagination` in `dab-config.json`. The defaults are small; this repo sets:

```json
"max-page-size": 100000,
"default-page-size": 100000
```

Silent truncation is a correctness bug — if you lower this deliberately, say so
in the entity description so the model can warn the user.

### The model picks the wrong entity, or invents columns

This is a description problem, not a code problem.

```bash
python scripts/inspect_server.py --fields
```

That prints exactly what the model sees. Any field without a description is a
field the model is guessing about. See
[`02-configuring-dab.md`](02-configuring-dab.md#writing-field-descriptions).

---

## Deployment

### `deploy.ps1` refuses to run

Working as intended. Your config has `"provider": "Unauthenticated"`, which on a
public ingress means an open database.

Either fix it — [`04-deploy-to-azure.md` section 2](04-deploy-to-azure.md#2-turning-authentication-on)
— or pass `-AllowUnauthenticated` if this is genuinely a throwaway demo over
synthetic data.

### `az acr build` fails

The build context needs both the `Dockerfile` and `dab-config.json`. The script
stages them into `deploy/_build`; if you are building by hand, copy the config
next to the Dockerfile first.

### The container app is running but unreachable

```bash
az containerapp show --name sql-mcp-server --resource-group rg-sql-mcp \
  --query "properties.configuration.ingress"
```

`external` should be `true` and `targetPort` should be `5000`.

### 401 on everything after enabling authentication

Expected unless you are sending a token. If a token *is* being sent:

- `audience` must match the app registration client ID exactly
- `issuer` must end in `/v2.0`
- the token must carry a role that appears in the entity's permissions

Decode the token at [jwt.ms](https://jwt.ms) and read the `roles` claim.

### Reading the container logs

```bash
az containerapp logs show --name sql-mcp-server --resource-group rg-sql-mcp --follow
```

---

## Still stuck?

- [SQL MCP Server hub](https://learn.microsoft.com/sql/mcp/)
- [Data API builder issues](https://github.com/Azure/data-api-builder/issues)
- [Data API builder discussions](https://github.com/Azure/data-api-builder/discussions)

When reporting a problem, include: the DAB version (`dab --version`), whether
you are local or deployed, and the server-side log line — not just the client
error, which is usually the least informative part.
