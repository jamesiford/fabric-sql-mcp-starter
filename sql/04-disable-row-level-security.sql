/*
    04-disable-row-level-security.sql
    ---------------------------------------------------------------------------
    Turn the security policy off.

    Run this when you want to:

      - try the optional Fabric data agent comparison (stage three), which
        cannot set session context and therefore sees zero rows while the
        policy is enabled
      - inspect the raw data yourself without presenting a role claim
      - start over from 03-row-level-security.sql

    Re-enable at any time by running sql/03-row-level-security.sql again. The
    two scripts are the on and off switch for the same feature; neither touches
    your data.
*/

SET NOCOUNT ON;
GO

DROP SECURITY POLICY IF EXISTS dbo.rls_rm_book;
DROP FUNCTION IF EXISTS dbo.fn_rm_book_predicate;
GO

PRINT 'Row-level security is OFF. Every caller now sees the whole book.';
PRINT 'Re-enable with: sql/03-row-level-security.sql';
GO
