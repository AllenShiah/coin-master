-- Coin-Master schema for Supabase / PostgreSQL.
-- Run this in the Supabase SQL editor (or `psql`) to create the tables the
-- backend expects. Designed around the Features spec:
--   * a loan always links a DEBTOR (borrower / 借入人) to a CREDITOR (債權人);
--   * amounts are signed -- borrow > 0, repay < 0 (借款正數 / 還款負數);
--   * each transaction carries its own annual rate (rates are never blended);
--   * webhook events are deduplicated to keep delivery idempotent;
--   * closed statement months can be cached so interest is not recomputed.

-- 1) Users -- one row per LINE userId (both debtors and creditors live here).
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    line_user_id  TEXT NOT NULL UNIQUE,            -- source.userId, U[0-9a-f]{32}
    display_name  TEXT,
    picture_url   TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2) Raw webhook events -- idempotency + audit log, not a business table.
CREATE TABLE IF NOT EXISTS line_webhook_events (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    webhook_event_id  TEXT NOT NULL UNIQUE,        -- LINE webhookEventId (dedupe key)
    line_message_id   TEXT,                         -- message.id (secondary dedupe)
    line_user_id      TEXT NOT NULL,
    event_type        TEXT NOT NULL,                -- message / follow / postback ...
    message_type      TEXT,                         -- text / sticker / image ...
    raw_text          TEXT,                         -- message.text as received
    raw_payload       JSONB NOT NULL,               -- whole event, for debugging
    line_timestamp    TIMESTAMPTZ NOT NULL,         -- event.timestamp (epoch ms)
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    parse_status      TEXT NOT NULL DEFAULT 'pending',  -- pending/parsed/failed/ignored
    parse_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_user ON line_webhook_events(line_user_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON line_webhook_events(parse_status);

-- 3) Loan transactions -- the core ledger. One row per borrow/repay.
CREATE TABLE IF NOT EXISTS loan_transactions (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    debtor_user_id    BIGINT NOT NULL REFERENCES users(id),     -- 借入人 (sender)
    creditor_user_id  BIGINT NOT NULL REFERENCES users(id),     -- 債權人 (@ mention)
    source_event_id   BIGINT REFERENCES line_webhook_events(id),
    transaction_type  TEXT NOT NULL CHECK (transaction_type IN ('borrow', 'repay')),
    amount            NUMERIC(16,2) NOT NULL,                   -- signed: +borrow / -repay
    annual_rate       NUMERIC(8,5) NOT NULL CHECK (annual_rate >= 0),  -- fraction, e.g. 0.0292
    transaction_date  DATE NOT NULL,                            -- date parsed from the text
    note              TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT amount_sign_matches_type CHECK (
        (transaction_type = 'borrow' AND amount > 0) OR
        (transaction_type = 'repay'  AND amount < 0)
    ),
    CONSTRAINT debtor_is_not_creditor CHECK (debtor_user_id <> creditor_user_id)
);

CREATE INDEX IF NOT EXISTS idx_loan_tx_debtor_date
    ON loan_transactions(debtor_user_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_loan_tx_creditor_date
    ON loan_transactions(creditor_user_id, transaction_date);

-- 4) Monthly interest cache -- one row per (debtor, creditor, payable month).
--    Only *closed* statement months are stored (they can never change again);
--    the current, still-accruing month is always computed live.
CREATE TABLE IF NOT EXISTS monthly_interest (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    debtor_user_id    BIGINT NOT NULL REFERENCES users(id),
    creditor_user_id  BIGINT NOT NULL REFERENCES users(id),
    year_month        DATE NOT NULL,               -- first day of payable month, e.g. 2026-06-01
    closing_balance   NUMERIC(16,2) NOT NULL,      -- principal owed at the 20th
    interest_amount   NUMERIC(16,2) NOT NULL,      -- interest due for that statement month
    calculated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (debtor_user_id, creditor_user_id, year_month)
);

-- Keep users.updated_at fresh on any change.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS users_set_updated_at ON users;
CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
