# Using a different database

This repo is written for **Fabric SQL Database**, but nothing in it is specific
to Fabric. Data API builder treats the whole SQL Server family the same way, and
`database-type` is `mssql` for all of them.

| Target | Works | Seed scripts | Row-level security | Notes |
| --- | --- | --- | --- | --- |
| **Fabric SQL Database** | yes | as written | documented | The path this repo is written for |
| **Azure SQL Database** | yes | as written | documented | Identical engine |
| **SQL Managed Instance** | yes | as written | documented | Identical engine |
| **SQL Server** (on-prem or VM) | yes | as written | documented | Network reachability is the only extra concern |
| **Fabric Warehouse** | yes | mostly | supported | See the caveats below |
| **Fabric Lakehouse** (SQL analytics endpoint) | yes | **no** | works, but undocumented | See the caveats below |

---

## Azure SQL, Managed Instance, SQL Server

No changes at all beyond `.env`:

```ini
SQL_SERVER=myserver.database.windows.net
SQL_DATABASE=deposits
```

Everything else — schema, seed, views, security policy, `dab-config.json` — runs
unchanged, because these are the same engine Fabric SQL Database runs.

### For SQL Server on-premises

Two extra considerations:

1. **Authentication.** This repo uses `Authentication=ActiveDirectoryDefault`
   throughout. If your SQL Server is not Entra-joined you will need a different
   mode. Change it in one place — `src/db.py` — and in the connection string
   passed to DAB.

2. **Reachability from Azure.** If you deploy the container to Azure Container
   Apps but the database is in your datacentre, the container must be able to
   reach it: VNet integration plus ExpressRoute, a VPN, or a private endpoint.
   This is usually the larger piece of work, and is worth confirming before
   anything else.

---

## Fabric Warehouse

Works, with two adjustments.

### Identity columns and constraints

Fabric Warehouse does not support every T-SQL construct Azure SQL does. In
`sql/01-schema.sql`, foreign key constraints are accepted but **not enforced** —
they are metadata only. The seed generates referentially valid data regardless,
so this is informational rather than a problem.

### `database-type`

Microsoft's documentation indicates `dwsql` for Warehouse and Lakehouse
endpoints:

```json
"data-source": { "database-type": "dwsql" }
```

**Test aggregation before relying on this.** See the caveat below.

---

## Fabric Lakehouse SQL analytics endpoint

Works for reading, but it is not a good first target and the seed scripts do not
apply.

### The seed does not work

A Lakehouse SQL analytics endpoint is **read-only**. You cannot `CREATE TABLE`
or `INSERT` through it — data arrives via Spark, pipelines or shortcuts.

To use the sample data with a Lakehouse you would need to load the four tables
through a notebook or pipeline first, then create the three views. That is a
meaningful piece of extra work, and it is the main reason this repo targets
Fabric SQL Database instead.

If you already have data in a Lakehouse, you only need `sql/02-views.sql`
adapted to your table names.

### The `dwsql` caveat

Microsoft's documentation indicates `dwsql` for Fabric endpoints. In testing
against a Lakehouse SQL analytics endpoint in September 2026:

- `dwsql` broke aggregation with `Invalid column name` errors
- `dwsql` showed numeric precision differences on some sums
- **`mssql` worked correctly** for both

If you target a Lakehouse and aggregation misbehaves, try:

```json
"data-source": { "database-type": "mssql" }
```

This may well be fixed by the time you read it. Test both and use the one that
returns correct results — verify against a known total, not just "it returned
something."

### Row-level security

`CREATE SECURITY POLICY` works against a Lakehouse SQL analytics endpoint, and
`sp_set_session_context` does reach the predicate. We measured it working.

But note the documentation position: Microsoft documents session context for the
SQL Server and Azure SQL families, and documents Fabric row-level security with
examples keyed off the *connected principal* rather than session context. The
combination works, but you are outside documented territory.

On Fabric SQL Database you are not, which matters if this has to pass a security
review.

---

## Other database engines

Data API builder also supports PostgreSQL, MySQL and Cosmos DB. The MCP tool
surface and `dab-config.json` structure are the same, but two things in this
repo are SQL Server specific:

| Piece | Portability |
| --- | --- |
| `sql/*.sql` | T-SQL. Would need rewriting. |
| `sp_set_session_context` RLS | SQL Server specific. PostgreSQL has its own row-level security with a different mechanism. |
| `dab-config.json` | Portable — change `database-type` and the entity sources |
| `scripts/*.py` | Portable — swap `pyodbc` for the relevant driver in `src/db.py` |

The architecture holds. The scripts would need work.

---

## A checklist when switching targets

1. Update `SQL_SERVER` and `SQL_DATABASE` in `.env`
2. Confirm `database-type` in `dab-config.json` — `mssql` unless you have tested
   otherwise
3. Run `python scripts/setup.py` (or load data another way if the target is
   read-only)
4. Run `python scripts/verify.py` — **check a total you can verify independently**
5. Run `python scripts/inspect_server.py` — confirm three tools and full
   descriptions
6. If using row-level security, run `python scripts/verify_rls.py`

Step 4 is the one people skip. An aggregation that returns *a* number is not the
same as an aggregation that returns the *right* number, and that is precisely
where the `dwsql` issue hides.

---

## Reference

- [Data API builder — supported databases](https://learn.microsoft.com/azure/data-api-builder/overview)
- [SQL database in Microsoft Fabric](https://learn.microsoft.com/fabric/database/sql/overview)
- [Lakehouse SQL analytics endpoint](https://learn.microsoft.com/fabric/data-engineering/lakehouse-sql-analytics-endpoint)
- [Fabric Warehouse T-SQL surface area](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area)
