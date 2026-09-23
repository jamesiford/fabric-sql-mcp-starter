/*
    03-row-level-security.sql
    ---------------------------------------------------------------------------
    Per-user row-level security, driven by the caller's role claim.

    Run this LAST, after 02-views.sql.

    WHAT THIS DOES
    Each relationship manager sees only their own customers. The filter is
    applied by the database, below the API, so no caller - not the model, not
    the MCP client, not a misconfigured orchestrator - can bypass it.

    HOW IT WORKS
    Data API builder is configured with:

        "options": { "set-session-context": true }

    With that set, DAB emits a call like this before every query, carrying the
    claims from the caller's validated token:

        EXEC sp_set_session_context
            'http://schemas.microsoft.com/ws/2008/06/identity/claims/role',
            @session_param0;

    The predicate below reads that value back with SESSION_CONTEXT() and
    compares it to the relationship_manager column. A security policy attaches
    the predicate to the view.

    WHY THIS IS FAIL-CLOSED
    There is no "if no claim is present, show everything" branch. A caller with
    no role claim sees zero rows. That is deliberate: the failure mode of a
    misconfiguration should be an empty result, not a full table scan of
    somebody else's book.

    THE BREAK-GLASS ROLE
    'svc-all' sees everything. It exists so an unattended service - a nightly
    report, a health check - can read the whole book without impersonating a
    person. Treat it as a privileged role and grant it sparingly.

    ------------------------------------------------------------------------
    IMPORTANT IF YOU ARE ALSO TRYING THE FABRIC DATA AGENT COMPARISON
    ------------------------------------------------------------------------
    A Fabric data agent has no mechanism to set session context. With this
    policy enabled, the data agent sees ZERO ROWS and will tell you the query
    returned no data. That is this policy working correctly, not a bug.

    You must choose one demo at a time:
        this script                        -> per-user security works
        04-disable-row-level-security.sql  -> data agent comparison works

    See docs/06-optional-data-agent.md.
*/

SET NOCOUNT ON;
GO

DROP SECURITY POLICY IF EXISTS dbo.rls_rm_book;
DROP FUNCTION IF EXISTS dbo.fn_rm_book_predicate;
GO

/*
    The predicate. An inline table-valued function returning one row when the
    caller may see the record, and no rows when they may not.

    WITH SCHEMABINDING is required for a security predicate.
*/
CREATE FUNCTION dbo.fn_rm_book_predicate(@rm AS VARCHAR(100))
RETURNS TABLE
WITH SCHEMABINDING
AS RETURN
    SELECT 1 AS is_visible
    WHERE
        -- The caller's role claim matches the row's relationship manager.
        CAST(SESSION_CONTEXT(N'http://schemas.microsoft.com/ws/2008/06/identity/claims/role')
             AS VARCHAR(200)) = @rm
        -- ...or the caller holds the break-glass service role.
        OR CAST(SESSION_CONTEXT(N'http://schemas.microsoft.com/ws/2008/06/identity/claims/role')
             AS VARCHAR(200)) = 'svc-all';
GO

/*
    Attach the predicate to the published view.

    The FILTER PREDICATE argument names the column passed to the function, so
    the policy reads as: "filter dbo.vw_deposits by comparing its
    relationship_manager column against the caller's claim."

    vw_maturity_ladder and vw_customer_exposure both select FROM vw_deposits,
    so they inherit this filter automatically. Add explicit predicates only if
    you later point them at base tables instead.
*/
CREATE SECURITY POLICY dbo.rls_rm_book
ADD FILTER PREDICATE dbo.fn_rm_book_predicate(relationship_manager)
    ON dbo.vw_deposits
WITH (STATE = ON);
GO

PRINT 'Row-level security is ON and fail-closed.';
PRINT '';
PRINT 'Verify with:  python scripts/verify_rls.py';
PRINT 'Expected:     svc-all sees all rows, each RM sees only their own,';
PRINT '              a caller with no role claim sees zero.';
PRINT '';
PRINT 'A plain connection (no session context) now returns 0 rows from';
PRINT 'dbo.vw_deposits. That is correct. It is not a broken connection.';
GO
