/*
    01-schema.sql
    ---------------------------------------------------------------------------
    Base tables for the synthetic commercial deposit book.

    Run this FIRST, against an empty Fabric SQL database (or any SQL Server
    family database). It is idempotent: re-running drops and recreates, so you
    can iterate without cleaning up by hand.

    Shape of the model, in plain terms:

        branch            one row per physical branch
        product           one row per deposit product type
        customer          one row per legal entity; may roll up to a parent
        deposit_account   one row per account, per snapshot date

    deposit_account is the fact table. Everything else is a dimension.

    NOTE ON WHAT THE AGENT SEES: none of these tables are published to the MCP
    server. Only the views created in 02-views.sql are. That separation is the
    security boundary - see docs/02-configuring-dab.md.
*/

SET NOCOUNT ON;
GO

/*
    Drop in dependency order.

    The views are SCHEMABINDING, which is required for the row-level security
    predicate to bind to them - but it also means SQL Server will refuse to drop
    the base tables while the views exist. And the security policy holds a
    reference to the view, so that has to go first in turn.

    So the teardown order is the reverse of the build order:

        security policy -> predicate function -> views -> tables

    Without this, a second run of this script fails with:
        Cannot DROP TABLE 'dbo.deposit_account' because it is being
        referenced by object 'vw_deposits'
*/
DROP SECURITY POLICY IF EXISTS dbo.rls_rm_book;
GO
DROP FUNCTION IF EXISTS dbo.fn_rm_book_predicate;
GO
DROP VIEW IF EXISTS dbo.vw_customer_exposure;
GO
DROP VIEW IF EXISTS dbo.vw_maturity_ladder;
GO
DROP VIEW IF EXISTS dbo.vw_deposits;
GO

DROP TABLE IF EXISTS dbo.deposit_account;
DROP TABLE IF EXISTS dbo.customer;
DROP TABLE IF EXISTS dbo.product;
DROP TABLE IF EXISTS dbo.branch;
GO

CREATE TABLE dbo.branch (
    branch_code      VARCHAR(10)   NOT NULL,
    branch_name      VARCHAR(100)  NOT NULL,
    city             VARCHAR(100)  NOT NULL,
    state_or_region  VARCHAR(10)   NOT NULL,   -- CA, SH, HK
    country          VARCHAR(2)    NOT NULL,   -- US, CN, HK
    CONSTRAINT pk_branch PRIMARY KEY (branch_code)
);
GO

CREATE TABLE dbo.product (
    product_code     VARCHAR(10)   NOT NULL,   -- CD, DDA, NOW, MMA, FX
    product_name     VARCHAR(100)  NOT NULL,
    is_term          BIT           NOT NULL,   -- only CD is a term product
    CONSTRAINT pk_product PRIMARY KEY (product_code)
);
GO

CREATE TABLE dbo.customer (
    customer_id         VARCHAR(20)   NOT NULL,
    customer_name       VARCHAR(200)  NOT NULL,
    -- Self-referencing: a subsidiary points at its group parent. NULL means the
    -- customer is its own parent. This is what makes group-exposure questions
    -- possible without a separate hierarchy table.
    parent_customer_id  VARCHAR(20)   NULL,
    segment             VARCHAR(50)   NOT NULL,
    industry            VARCHAR(100)  NOT NULL,
    relationship_manager VARCHAR(100) NOT NULL,
    CONSTRAINT pk_customer PRIMARY KEY (customer_id)
);
GO

CREATE TABLE dbo.deposit_account (
    account_number   VARCHAR(20)     NOT NULL,
    customer_id      VARCHAR(20)     NOT NULL,
    branch_code      VARCHAR(10)     NOT NULL,
    product_code     VARCHAR(10)     NOT NULL,
    currency         VARCHAR(3)      NOT NULL,   -- USD, CNY, HKD, JPY, EUR
    balance          DECIMAL(19, 2)  NOT NULL,
    rate             DECIMAL(6, 3)   NOT NULL,   -- percentage, e.g. 3.907
    opened_on        DATE            NOT NULL,
    matures_on       DATE            NULL,       -- NULL for everything except CD
    as_of_date       DATE            NOT NULL,   -- reporting snapshot
    CONSTRAINT pk_deposit_account PRIMARY KEY (account_number),
    CONSTRAINT fk_da_customer FOREIGN KEY (customer_id) REFERENCES dbo.customer (customer_id),
    CONSTRAINT fk_da_branch   FOREIGN KEY (branch_code) REFERENCES dbo.branch (branch_code),
    CONSTRAINT fk_da_product  FOREIGN KEY (product_code) REFERENCES dbo.product (product_code)
);
GO

-- Indexes matching how the published views are actually queried: aggregation
-- by branch, by product and by relationship manager, and maturity windows.
CREATE INDEX ix_da_branch    ON dbo.deposit_account (branch_code)  INCLUDE (balance, currency);
CREATE INDEX ix_da_product   ON dbo.deposit_account (product_code) INCLUDE (balance, rate);
CREATE INDEX ix_da_customer  ON dbo.deposit_account (customer_id)  INCLUDE (balance, currency, product_code);
CREATE INDEX ix_da_matures   ON dbo.deposit_account (matures_on)   INCLUDE (balance, branch_code);
GO

-- Reference data is small and fixed, so it lives here rather than in the
-- generator. Nine branches across three countries.
INSERT INTO dbo.branch (branch_code, branch_name, city, state_or_region, country) VALUES
    ('LA-001', 'Los Angeles Main',              'Los Angeles',   'CA', 'US'),
    ('LA-002', 'Monterey Park',                 'Monterey Park', 'CA', 'US'),
    ('LA-003', 'Pasadena',                      'Pasadena',      'CA', 'US'),
    ('LA-004', 'Torrance',                      'Torrance',      'CA', 'US'),
    ('SF-001', 'San Francisco Financial District','San Francisco','CA', 'US'),
    ('SF-002', 'Millbrae',                      'Millbrae',      'CA', 'US'),
    ('MOD-001','Modesto',                       'Modesto',       'CA', 'US'),
    ('HK-001', 'Hong Kong Central',             'Hong Kong',     'HK', 'HK'),
    ('CN-001', 'Shanghai',                      'Shanghai',      'SH', 'CN');
GO

-- Five products. CD is the only one with a maturity date, which is why the
-- maturity ladder view can filter on product_code = 'CD' with confidence.
INSERT INTO dbo.product (product_code, product_name, is_term) VALUES
    ('CD',  'Certificate of Deposit',          1),
    ('DDA', 'Demand Deposit Account',          0),
    ('NOW', 'Negotiable Order of Withdrawal',  0),
    ('MMA', 'Money Market Account',            0),
    ('FX',  'Foreign Currency Deposit',        0);
GO

PRINT 'Schema created. Reference data loaded: 9 branches, 5 products.';
PRINT 'Next: run scripts/seed.py to generate customers and accounts.';
GO
