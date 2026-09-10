-- Retreat Finance Ops — SQLite schema
-- Generated to match docs/SCHEMA.md §4. Applied by data/build_dataset.py.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS retreat_budget_provenance;
DROP TABLE IF EXISTS provenance_sources;
DROP TABLE IF EXISTS sec_benchmarks;
DROP TABLE IF EXISTS audit_findings;
DROP TABLE IF EXISTS reconciliation_matches;
DROP TABLE IF EXISTS bank_transactions;
DROP TABLE IF EXISTS bills_ap;
DROP TABLE IF EXISTS invoices_ar;
DROP TABLE IF EXISTS retreats;
DROP TABLE IF EXISTS vendors;
DROP TABLE IF EXISTS clients;

CREATE TABLE clients (
    client_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    industry_category    TEXT NOT NULL,
    contract_start_date  DATE NOT NULL
);

CREATE TABLE vendors (
    vendor_id           TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL CHECK (category IN ('venue','catering','travel','activities','other')),
    payment_terms_days  INTEGER NOT NULL CHECK (payment_terms_days >= 0)
);

CREATE TABLE retreats (
    retreat_id         TEXT PRIMARY KEY,
    client_id          TEXT NOT NULL REFERENCES clients(client_id),
    destination        TEXT NOT NULL,
    headcount          INTEGER NOT NULL CHECK (headcount BETWEEN 1 AND 500),
    start_date         DATE NOT NULL,
    end_date           DATE NOT NULL,
    budget_venue       REAL NOT NULL CHECK (budget_venue      >= 0),
    budget_catering    REAL NOT NULL CHECK (budget_catering   >= 0),
    budget_travel      REAL NOT NULL CHECK (budget_travel     >= 0),
    budget_activities  REAL NOT NULL CHECK (budget_activities >= 0),
    budget_other       REAL NOT NULL CHECK (budget_other      >= 0),
    budget_total       REAL NOT NULL CHECK (budget_total      >= 0),
    CHECK (end_date >= start_date)
);

CREATE TABLE invoices_ar (
    invoice_id    TEXT PRIMARY KEY,
    client_id     TEXT NOT NULL REFERENCES clients(client_id),
    retreat_id    TEXT NOT NULL REFERENCES retreats(retreat_id),
    invoice_date  DATE NOT NULL,
    due_date      DATE NOT NULL,
    amount        REAL NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('open','paid','partial','void')),
    payment_date  DATE,
    source_ref    TEXT NOT NULL,
    line_role     TEXT CHECK (line_role IN ('deposit','final','addon'))
);

CREATE TABLE bills_ap (
    bill_id       TEXT PRIMARY KEY,
    vendor_id     TEXT NOT NULL REFERENCES vendors(vendor_id),
    retreat_id    TEXT NOT NULL REFERENCES retreats(retreat_id),
    category      TEXT NOT NULL CHECK (category IN ('venue','catering','travel','activities','other')),
    bill_date     DATE NOT NULL,
    due_date      DATE NOT NULL,
    amount        REAL NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('open','paid','partial','void')),
    payment_date  DATE,
    source_ref    TEXT NOT NULL
);

CREATE TABLE bank_transactions (
    transaction_id    TEXT PRIMARY KEY,
    transaction_date  DATE NOT NULL,
    description       TEXT NOT NULL,
    amount            REAL NOT NULL,
    running_balance   REAL,
    source_ref        TEXT NOT NULL
);

CREATE TABLE reconciliation_matches (
    match_id          TEXT PRIMARY KEY,
    transaction_id    TEXT NOT NULL REFERENCES bank_transactions(transaction_id),
    matched_type      TEXT NOT NULL CHECK (matched_type IN ('invoice','bill')),
    matched_id        TEXT NOT NULL,
    confidence_score  REAL NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    match_method      TEXT NOT NULL
);

CREATE TABLE audit_findings (
    finding_id    TEXT PRIMARY KEY,
    finding_type  TEXT NOT NULL CHECK (finding_type IN ('duplicate','stale_90plus','unexplained_txn','double_payment')),
    related_ids   TEXT NOT NULL,
    description   TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    date_found    DATE NOT NULL
);

CREATE TABLE sec_benchmarks (
    company_name   TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    fiscal_period  TEXT NOT NULL,
    ar_balance     REAL NOT NULL,
    ap_balance     REAL NOT NULL,
    revenue        REAL NOT NULL,
    computed_dso   REAL NOT NULL,
    computed_dpo   REAL NOT NULL,
    source_url     TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_period)
);

CREATE TABLE provenance_sources (
    source_id  TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('dataset','pricing','benchmark')),
    url        TEXT NOT NULL,
    accessed   DATE NOT NULL,
    notes      TEXT
);

CREATE TABLE retreat_budget_provenance (
    retreat_id  TEXT NOT NULL REFERENCES retreats(retreat_id),
    category    TEXT NOT NULL CHECK (category IN ('venue','catering','travel','activities','other')),
    source_id   TEXT NOT NULL REFERENCES provenance_sources(source_id),
    basis       TEXT NOT NULL,
    PRIMARY KEY (retreat_id, category)
);

CREATE INDEX ix_ar_due_status ON invoices_ar (due_date, status);
CREATE INDEX ix_ar_client     ON invoices_ar (client_id);
CREATE INDEX ix_ar_retreat    ON invoices_ar (retreat_id);
CREATE INDEX ix_ap_due_status ON bills_ap (due_date, status);
CREATE INDEX ix_ap_vendor     ON bills_ap (vendor_id);
CREATE INDEX ix_ap_category   ON bills_ap (category);
CREATE INDEX ix_ap_retreat    ON bills_ap (retreat_id);
CREATE INDEX ix_txn_date      ON bank_transactions (transaction_date);
CREATE INDEX ix_txn_amount    ON bank_transactions (amount);
CREATE INDEX ix_match_txn     ON reconciliation_matches (transaction_id);
CREATE INDEX ix_match_target  ON reconciliation_matches (matched_type, matched_id);
