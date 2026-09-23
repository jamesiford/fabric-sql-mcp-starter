/*
    02-views.sql
    ---------------------------------------------------------------------------
    The curated views. THIS IS THE PUBLISHED SURFACE.

    Run this AFTER scripts/seed.py.

    Everything the MCP server can reach is defined here. The base tables from
    01-schema.sql are never published, so a caller cannot name them, cannot join
    them, and cannot reach a column that is not projected below.

    That makes these views the security boundary. If a column should never leave
    the bank, leave it out of vw_deposits and no configuration mistake further
    up the stack can expose it.

    Design note - why views and not tables:
      Microsoft's own guidance for SQL MCP Server recommends a view wherever
      joins or computed logic are needed. It also puts the domain logic in SQL,
      where a data team can review, version and own it, rather than inside
      application code that the data team never sees.
*/

SET NOCOUNT ON;
GO

DROP VIEW IF EXISTS dbo.vw_customer_exposure;
DROP VIEW IF EXISTS dbo.vw_maturity_ladder;
DROP VIEW IF EXISTS dbo.vw_deposits;
GO

/*
    vw_deposits - the general-purpose entity.

    One row per account. Customer, branch and product are pre-joined, so a model
    never has to infer a join, and never gets one wrong.

    SCHEMABINDING is required later: the row-level security predicate in
    03-row-level-security.sql binds to this view, and a schema-bound object
    cannot be altered out from under a security policy.
*/
CREATE VIEW dbo.vw_deposits
WITH SCHEMABINDING
AS
SELECT
    a.account_number,
    a.customer_id,
    c.customer_name,
    c.parent_customer_id,
    c.segment,
    c.industry,
    c.relationship_manager,
    a.product_code,
    p.product_name,
    a.currency,
    a.balance,
    a.rate,
    a.opened_on,
    a.matures_on,
    a.as_of_date,
    a.branch_code,
    b.branch_name,
    b.city,
    b.state_or_region,
    b.country
FROM dbo.deposit_account AS a
INNER JOIN dbo.customer AS c ON c.customer_id  = a.customer_id
INNER JOIN dbo.branch   AS b ON b.branch_code  = a.branch_code
INNER JOIN dbo.product  AS p ON p.product_code = a.product_code;
GO

/*
    vw_maturity_ladder - term deposits that have not yet matured.

    days_to_maturity is precomputed so a caller can ask for any horizon with an
    ordinary numeric filter:

        days_to_maturity le 90

    rather than needing date arithmetic, which is where generated queries most
    often go wrong. This is the single highest-value piece of modelling in the
    whole example: it converts a hard question into an easy filter.
*/
CREATE VIEW dbo.vw_maturity_ladder
AS
SELECT
    account_number,
    customer_id,
    customer_name,
    relationship_manager,
    branch_code,
    branch_name,
    city,
    country,
    product_code,
    product_name,
    currency,
    balance,
    rate,
    as_of_date,
    matures_on,
    DATEDIFF(day, as_of_date, matures_on) AS days_to_maturity
FROM dbo.vw_deposits
WHERE matures_on IS NOT NULL
  AND matures_on >= as_of_date;
GO

/*
    vw_customer_exposure - per-customer rollup.

    Pre-aggregated by currency and product so "how much does customer X hold"
    does not read raw accounts. parent_customer_id is projected so group
    relationships can be rolled up by the caller.

    Note the currency split: balances are NOT summed across currencies here,
    because doing so silently would be wrong. The grain is deliberately
    customer x currency x product.
*/
CREATE VIEW dbo.vw_customer_exposure
AS
SELECT
    customer_id,
    customer_name,
    parent_customer_id,
    segment,
    industry,
    relationship_manager,
    currency,
    product_code,
    product_name,
    SUM(balance)  AS total_balance,
    COUNT_BIG(*)  AS account_count,
    AVG(rate)     AS avg_rate
FROM dbo.vw_deposits
GROUP BY
    customer_id,
    customer_name,
    parent_customer_id,
    segment,
    industry,
    relationship_manager,
    currency,
    product_code,
    product_name;
GO

PRINT 'Views created: vw_deposits, vw_maturity_ladder, vw_customer_exposure.';
PRINT 'These three are the entire published surface. Base tables stay private.';
GO
