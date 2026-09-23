# Deploying to Azure Container Apps

Your MCP server currently runs on your laptop. This guide puts it on a URL that
Copilot Studio, Microsoft Foundry, or your own application can reach.

It follows Microsoft's own
[Azure Container Apps quickstart](https://learn.microsoft.com/azure/data-api-builder/mcp/quickstart-azure-container-apps),
with two deliberate differences:

| | Microsoft's quickstart | This guide |
| --- | --- | --- |
| Database auth | SQL username and password in the connection string | **Managed identity** — no password exists |
| Registry auth | Registry username and password | **Managed identity** — no password exists |

Both changes come straight from the "security best practices" section of that
same quickstart. Doing them from the start is easier than retrofitting them.

---

## ⚠️ Read this first

Your config currently says:

```json
"authentication": { "provider": "Unauthenticated" }
```

On `localhost` that is fine — only you can reach the port.

**On a public Container Apps ingress it means anyone who discovers the URL can
read every row you have published.** No password, no token, nothing.

Container Apps URLs are not secret. They are predictable, they appear in
certificate transparency logs, and they get scanned.

`deploy.ps1` **refuses to run** against an unauthenticated config unless you
explicitly pass `-AllowUnauthenticated`. That override exists only for a
throwaway demo over synthetic data.

Section 2 below fixes it properly. Do that part.

---

## 1. Deploy the container

### What you need

- An Azure subscription
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), signed in with `az login`
- A working local setup — you should have run `python scripts/verify.py` successfully

Docker is **not** required. The image is built in Azure by `az acr build`.

### For a quick synthetic-data demo

```powershell
./deploy/deploy.ps1 `
    -ResourceGroup rg-sql-mcp `
    -Location eastus `
    -SqlServer   "<your-server>.database.fabric.microsoft.com" `
    -SqlDatabase "deposits" `
    -AllowUnauthenticated
```

Only do this with the synthetic seed data, and delete it afterwards.

### For anything else

Complete section 2 first, then run the same command **without**
`-AllowUnauthenticated`.

### What the script creates

| Resource | Purpose | Rough cost |
| --- | --- | --- |
| Resource group | Container for everything | free |
| Container registry (Basic) | Holds your image | ~$5/month |
| Container Apps environment | Hosting | free |
| Container app | 0.5 vCPU, 1 GiB, 1–3 replicas | ~$15–30/month at 1 replica |

Set `--min-replicas 0` to scale to zero when idle, trading a cold start for near-zero cost.

It is idempotent: re-run after a failure and it reuses whatever already exists.

### Grant the container access to your database

The deployment creates a managed identity but **cannot grant it database
access** — only a database admin can do that. This is the one manual step.

1. Open [`deploy/grant-managed-identity.sql`](../deploy/grant-managed-identity.sql)
2. Replace `<MANAGED-IDENTITY-NAME>` with your app name (default: `sql-mcp-server`)
3. Run it against your database, as yourself, in the Fabric query editor or SSMS

```sql
CREATE USER [sql-mcp-server] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [sql-mcp-server];
```

Until you do this, the container starts but every query fails with a login
error. That is expected.

### Confirm it works

```bash
python scripts/inspect_server.py --url https://<your-app>.azurecontainerapps.io/mcp
```

This exercises the real MCP protocol and shows exactly which tools are
advertised: three tools, no write tools.

> `curl /health` returns **403** when `host.mode` is `production` — Data API
> builder restricts that endpoint outside development mode. That is expected,
> and is why the check above uses the MCP endpoint instead.

---

## 2. Turning authentication on

This is the part that makes the deployment defensible.

### 2.1 Register an application

```bash
az ad app create --display-name "SQL MCP Server" --query appId --output tsv
```

Keep the returned **application (client) ID** and your **tenant ID**:

```bash
az account show --query tenantId --output tsv
```

### 2.2 Define roles the predicate can use

The roles you define here should match what your row-level security predicate
expects. With the sample data, that means one role per relationship manager
plus the break-glass service role.

In the Azure portal: **Microsoft Entra ID → App registrations → your app →
App roles → Create app role**.

| Display name | Value | Who it is for |
| --- | --- | --- |
| Relationship Manager | `RelationshipManager` | A named banker |
| Service (all data) | `svc-all` | Unattended jobs — grant sparingly |

### 2.3 Update `dab-config.json`

```json
"host": {
  "mode": "production",
  "cors": { "origins": [], "allow-credentials": false },
  "authentication": {
    "provider": "AzureAD",
    "jwt": {
      "audience": "<application-client-id>",
      "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0"
    }
  }
}
```

### 2.4 Replace `anonymous` on every entity

This is easy to forget, and forgetting it undoes the previous step. Every
entity, not just the first:

```json
"permissions": [
  { "role": "RelationshipManager", "actions": [ { "action": "read" } ] },
  { "role": "svc-all",             "actions": [ { "action": "read" } ] }
]
```

With `anonymous` gone, a caller without a valid token gets nothing.

### 2.5 Redeploy

```powershell
./deploy/deploy.ps1 -ResourceGroup rg-sql-mcp `
    -SqlServer "<your-server>" -SqlDatabase "deposits"
```

No `-AllowUnauthenticated` this time. If the script still refuses, the config
change did not save.

### 2.6 Prove it

An unauthenticated request should now fail:

```bash
python scripts/inspect_server.py --url https://<your-app>.azurecontainerapps.io/mcp
# expect 401 Unauthorized
```

**That error is the success condition.** It means the server is no longer
readable by strangers.

---

## 3. How authentication and row-level security connect

This is the part worth understanding, because the two halves only work together.

```
Caller signs in
    └─> Entra issues a token carrying  roles: ["B. Nakamura"]
            └─> DAB validates the token against audience + issuer
                    └─> DAB checks the entity permits that role
                            └─> DAB emits sp_set_session_context with the claim
                                    └─> SQL predicate filters to that RM's rows
```

Each layer does one job:

| Layer | Question it answers |
| --- | --- |
| Entra | Who is this? |
| DAB permissions | May they use this entity at all? |
| Session context | Who is asking, expressed in SQL? |
| RLS predicate | Which rows may they see? |

The last one is enforced **by the database**, below the API. Even a bug in the
layers above cannot return rows the predicate excludes — which is why this is
worth explaining to a security reviewer in exactly this order.

---

## 4. Production hardening

Beyond authentication, in rough priority order:

| Item | Why |
| --- | --- |
| **Private endpoint to SQL** | Keeps database traffic off the public internet |
| **API Management in front** | Rate limiting, quotas, one audited ingress |
| **Narrow the SQL grant** | `GRANT SELECT` on the three views instead of `db_datareader` — see the comment in `grant-managed-identity.sql` |
| **OpenTelemetry** | Set `telemetry.open-telemetry.enabled` and point it at a collector; every tool call becomes traceable |
| **Restrict CORS** | Only if a browser calls the server directly |
| **Pin the image tag** | Already done in the Dockerfile — keep it that way |

---

## 5. When something is wrong

### The container starts, then every query fails

The managed identity grant has not been run, or the name does not match.

```bash
az containerapp logs show --name sql-mcp-server --resource-group rg-sql-mcp --follow
```

Look for `Login failed for user '<token-identified principal>'`.

### `/health` responds but `/mcp` does not

Check `runtime.mcp.enabled` is `true` and `path` is `/mcp`.

### 401 on every request after enabling auth

Expected, unless you are sending a token. Confirm:
- the `audience` matches the app registration's client ID exactly
- the `issuer` includes the `/v2.0` suffix
- the token carries a role that appears in the entity's permissions

### The image will not build

`az acr build` needs the Dockerfile and `dab-config.json` in the same
directory — the script handles this by staging them into `deploy/_build`. If
you build by hand, copy the config next to the Dockerfile first.

---

## Reference

- [SQL MCP Server hub](https://learn.microsoft.com/sql/mcp/)
- [Quickstart: Azure Container Apps](https://learn.microsoft.com/azure/data-api-builder/mcp/quickstart-azure-container-apps)
- [Authorization in Data API builder](https://learn.microsoft.com/azure/data-api-builder/concept/security/authorization)
- [Container Apps managed identity](https://learn.microsoft.com/azure/container-apps/managed-identity)
- [Entra authentication for Azure SQL](https://learn.microsoft.com/azure/azure-sql/database/authentication-aad-overview)
