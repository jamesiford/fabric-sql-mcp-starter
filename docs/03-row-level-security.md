# Row-level security

How one relationship manager sees only their own customers, enforced by the
database rather than by the application.

This is the capability that is hardest to retrofit and easiest to get subtly
wrong, so it is worth understanding rather than copying.

---

## The problem

A deposit book is not uniformly readable. B. Nakamura should see their own
customers. I. Petrosyan should see theirs. Neither should see the other's.

The naive approach is to filter in the application: read the user, add a
`WHERE relationship_manager = ...` clause. That fails for a specific reason —
**the filter is only as good as every code path that remembers to apply it.**
One forgotten clause, one new endpoint, one debugging shortcut, and the
protection is gone with nothing to detect it.

Row-level security moves the filter into the database, below every code path
that could forget.

---

## The chain

```
Caller signs in
    └─> Entra issues a token carrying  roles: ["B. Nakamura"]
            └─> DAB validates the token
                    └─> DAB emits sp_set_session_context with the role claim
                            └─> SQL predicate filters rows
                                    └─> only that RM's rows come back
```

Three pieces make it work, and all three are required.

### 1. DAB must be told to propagate claims

In `dab/dab-config.json`:

```json
"data-source": {
  "options": { "set-session-context": true }
}
```

**Off by default.** With it on, DAB emits this before every query:

```sql
EXEC sp_set_session_context
    'http://schemas.microsoft.com/ws/2008/06/identity/claims/role',
    @session_param0;
```

Note the claim key is the full WS-Federation URI, not a short name like `role`.
That is what DAB actually sends, and the predicate must match it exactly.

### 2. A predicate that reads the claim back

From `sql/03-row-level-security.sql`:

```sql
CREATE FUNCTION dbo.fn_rm_book_predicate(@rm AS VARCHAR(100))
RETURNS TABLE
WITH SCHEMABINDING
AS RETURN
    SELECT 1 AS is_visible
    WHERE CAST(SESSION_CONTEXT(N'http://schemas.microsoft.com/ws/2008/06/identity/claims/role')
               AS VARCHAR(200)) = @rm
       OR CAST(SESSION_CONTEXT(N'http://schemas.microsoft.com/ws/2008/06/identity/claims/role')
               AS VARCHAR(200)) = 'svc-all';
```

An inline table-valued function: return a row when the caller may see this
record, return nothing when they may not.

### 3. A policy binding the predicate to the view

```sql
CREATE SECURITY POLICY dbo.rls_rm_book
ADD FILTER PREDICATE dbo.fn_rm_book_predicate(relationship_manager)
    ON dbo.vw_deposits
WITH (STATE = ON);
```

`vw_maturity_ladder` and `vw_customer_exposure` both select **from**
`vw_deposits`, so they inherit the filter automatically. If you later repoint
them at base tables, they need their own predicates.

---

## Fail-closed, and why it matters

Look closely at the predicate: there is **no permissive branch**. Nothing says
"if no claim is present, show everything."

| Caller's role claim | Rows visible |
| --- | --- |
| `svc-all` | all of them |
| `B. Nakamura` | that manager's customers only |
| `I. Petrosyan` | a different, smaller set |
| *(none)* | **zero** |

A misconfiguration therefore produces an **empty result**, not a full scan of
somebody else's book. When something breaks at 2am, the failure mode is a
confused user rather than a disclosure incident.

The cost is that a plain `SELECT` from SSMS returns nothing, which looks alarming
the first time. It is correct. Present a claim first:

```sql
EXEC sp_set_session_context
    'http://schemas.microsoft.com/ws/2008/06/identity/claims/role', 'svc-all';
SELECT COUNT(*) FROM dbo.vw_deposits;
```

---

## The break-glass role

`svc-all` sees everything. It exists so an unattended process — a nightly
report, a reconciliation job, a health check — can read the whole book without
impersonating a person.

Treat it as privileged:

- grant it to service principals, not people
- do not make it the default for anything
- log its use if you can

It is the one line in the predicate worth reviewing periodically.

---

## Verifying it

```bash
python scripts/verify_rls.py
```

This presents several different role claims on the same connection and reports
what each one sees. The tool call is identical in every case — only the claim
changes.

Expected shape:

```
  svc-all              8,000 accounts
  B. Nakamura            812 accounts
  I. Petrosyan           794 accounts
  (no claim)               0 accounts
```

If every row is the same number, `set-session-context` is not enabled. Restart
DAB after changing it — config is read at startup only.

---

## Why this is worth more than it looks

Three properties are hard to get any other way:

1. **It cannot be bypassed from above.** By the time the query runs, the identity
   is bound to the session. A bug in the model, the MCP client or the
   orchestrator cannot return excluded rows.
2. **It is reviewable.** The predicate is a dozen lines of SQL your data team can
   read, version and reason about — not logic distributed across application
   code.
3. **It is testable.** `verify_rls.py` is a regression test for an entitlement
   rule, which is unusual and valuable.

---

## The limitation worth knowing

**A Fabric data agent cannot participate in this.**

It has no mechanism to set session context, so against this fail-closed policy
it sees zero rows and reports that the query returned no data. That is the
policy working correctly — but it means per-user entitlement is not reachable on
that path.

If you are trying the optional stage-three comparison, disable the policy first:

```
sql/04-disable-row-level-security.sql
```

and re-enable it afterwards by re-running `03`. You can demonstrate per-user
security or the data agent comparison — not both at the same time.

---

## Adapting it to your own data

The predicate compares a **claim** to a **column**. To adapt it:

1. Decide which column carries the entitlement. Here it is
   `relationship_manager`; in your data it might be `branch_code`,
   `business_unit`, or a customer ID.
2. Decide what the claim contains. The value must match the column exactly —
   claims are strings, and `"B. Nakamura"` does not match `"b.nakamura"`.
3. Change the predicate's comparison and the `ADD FILTER PREDICATE` column.

For anything more complex than equality — a user seeing several branches, say —
join to a mapping table inside the predicate:

```sql
CREATE FUNCTION dbo.fn_branch_predicate(@branch AS VARCHAR(10))
RETURNS TABLE
WITH SCHEMABINDING
AS RETURN
    SELECT 1 AS is_visible
    FROM dbo.user_branch_access AS a
    WHERE a.branch_code = @branch
      AND a.user_principal =
          CAST(SESSION_CONTEXT(N'http://schemas.microsoft.com/ws/2008/06/identity/claims/role')
               AS VARCHAR(200));
```

Keep the predicate cheap — it runs on every row of every query. Index the
mapping table on the column being compared.

---

## Reference

- [Row-level security in SQL Server](https://learn.microsoft.com/sql/relational-databases/security/row-level-security)
- [`sp_set_session_context`](https://learn.microsoft.com/sql/relational-databases/system-stored-procedures/sp-set-session-context-transact-sql)
- [Authorization in Data API builder](https://learn.microsoft.com/azure/data-api-builder/concept/security/authorization)
