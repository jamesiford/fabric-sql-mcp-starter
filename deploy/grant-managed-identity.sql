/*
    grant-managed-identity.sql
    ---------------------------------------------------------------------------
    PART 1 OF 2. Give the deployed container permission to read your database.

    Run this ONCE, after deploy.ps1 has created the container app, and run it
    as yourself - you need to be an admin on the database.

    BEFORE RUNNING: replace <MANAGED-IDENTITY-NAME> below with the name printed
    by deploy.ps1. It is the container app's name, for example:

        sql-mcp-server

    ------------------------------------------------------------------------
    IF YOU ARE ON FABRIC SQL DATABASE, THIS IS NOT ENOUGH ON ITS OWN
    ------------------------------------------------------------------------
    Fabric has a permission layer above SQL. After running this you must also
    give the same identity access to the Fabric workspace or item, or the
    container will authenticate successfully and then be refused with:

        Login failed for user '<token-identified principal>'.
        Reason: Validation of user's permissions failed.
                Verify the user has the Read item permission.

    That message reads like a SQL problem. It is not - the grant below will
    already have succeeded. See docs/04-deploy-to-azure.md, part 2.

    For Azure SQL, SQL Managed Instance or SQL Server, this file is all you need.

    WHY THIS EXISTS
    The container authenticates to SQL as its own managed identity. That
    identity is a real principal in your Entra tenant, but it is a stranger to
    your database until you create a user for it. These two statements are that
    introduction.

    WHAT IT DOES NOT DO
    It does not grant write access. db_datareader is read-only, which is the
    second of three independent layers stopping a write:

        1. dml-tools in dab-config.json     - the tools do not exist
        2. db_datareader here               - the identity cannot write
        3. views, not tables                - there is nothing writable published

    Any one of these would be enough. Together they mean a mistake in one place
    is not an incident.
*/

-- Create a database user backed by the container's managed identity.
-- FROM EXTERNAL PROVIDER means "this principal lives in Entra, not in SQL".
CREATE USER [<MANAGED-IDENTITY-NAME>] FROM EXTERNAL PROVIDER;
GO

-- Read-only. Deliberately not db_datawriter, and deliberately not db_owner.
ALTER ROLE db_datareader ADD MEMBER [<MANAGED-IDENTITY-NAME>];
GO

/*
    OPTIONAL, BUT RECOMMENDED: narrow it further.

    db_datareader grants SELECT on everything in the database, including the
    base tables. The views are what you publish, so the identity does not need
    the tables. To grant only what is actually used, replace the ALTER ROLE
    above with:

        GRANT SELECT ON dbo.vw_deposits          TO [<MANAGED-IDENTITY-NAME>];
        GRANT SELECT ON dbo.vw_maturity_ladder   TO [<MANAGED-IDENTITY-NAME>];
        GRANT SELECT ON dbo.vw_customer_exposure TO [<MANAGED-IDENTITY-NAME>];

    Now even a misconfigured entity pointing at a base table returns a
    permission error rather than data. This is the version to use in production.
*/

PRINT 'Managed identity granted read access.';
PRINT 'Verify from the container:  curl https://<your-app>/health';
GO
