-- UNS schema contract — database side.
--
-- Mirror of contracts/equipment_hierarchy.yaml and contracts/tag_database.csv.
-- Aligned with the canonical TigerData UNS schema from
-- "Ask Your Factory Floor Anything: Structuring Industrial Data for AI Agents."
--
-- V7 direct-write architecture: collection clients (Modbus, OPC UA, MQTT) read
-- from their respective sources, resolve tag_id from uns_namespace, and insert
-- readings into tag_history directly:
--   INSERT INTO tag_history (ts, tag_id, value, quality)
--   ... ON CONFLICT (ts, tag_id) DO NOTHING
-- No standalone ingester; tag-id resolution and batch-upsert logic live in each
-- client.
--
-- See: https://www.tigerdata.com/blog/ask-your-factory-floor-anything-structuring-industrial-data-for-ai-agents

-- Clean-slate re-apply: each setup.sh run wipes all collected data and reseeds.
-- Intentional for the tutorial (every run starts fresh) — NOT a recovery mechanism.
DROP TABLE IF EXISTS dead_letter, tag_history, uns_namespace CASCADE;

-- 1. ISA-95 namespace + tag metadata. Not time-series — one row per unique
--    (enterprise, site, area, line, cell, tag_name) tuple.
CREATE TABLE uns_namespace (
    id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    enterprise      TEXT NOT NULL,
    site            TEXT NOT NULL,
    area            TEXT NOT NULL,
    line            TEXT NOT NULL,
    cell            TEXT NOT NULL,
    tag_name        TEXT NOT NULL,

    -- Auto-assembled full path. No application code constructs this ad hoc,
    -- and no two sites can drift on the format.
    uns_path        TEXT GENERATED ALWAYS AS (
                        enterprise || '/' || site || '/' || area || '/' ||
                        line || '/' || cell || '/' || tag_name
                    ) STORED,

    schema_version  TEXT NOT NULL DEFAULT 'v1',
    description     TEXT,
    metric_type     TEXT NOT NULL,      -- contract type: Float | Int32 | Boolean | String
    unit            TEXT,

    UNIQUE (enterprise, site, area, line, cell, tag_name)
);
CREATE UNIQUE INDEX idx_uns_namespace_path ON uns_namespace (uns_path);


-- 2. Time-series readings. Narrow fact table, FK-enforced against the namespace.
--    Quality is preserved from the client (GOOD | BAD | STALE) so downstream
--    consumers can filter or weight accordingly.
CREATE TABLE tag_history (
    ts          TIMESTAMPTZ      NOT NULL,
    tag_id      INT              NOT NULL REFERENCES uns_namespace (id),
    value       DOUBLE PRECISION NOT NULL,
    quality     TEXT             NOT NULL DEFAULT 'GOOD'
);

-- Dedup + device-time query acceleration in a single index.
-- Clients batch-upsert with INSERT ... ON CONFLICT (ts, tag_id) DO NOTHING (logic in shared/db.py).
CREATE UNIQUE INDEX idx_tag_history_dedup ON tag_history (ts, tag_id);

-- Convert to hypertable; 1-day chunks is a safe starting point for plant telemetry.
SELECT create_hypertable(
    'tag_history',
    'ts',
    chunk_time_interval => INTERVAL '1 day'
);


-- 3. Dead-letter table. Rows rejected by client governance checks land here
--    (unknown tag, malformed payload, missing timestamp) so they can be
--    investigated instead of silently dropped.
CREATE TABLE dead_letter (
    id          BIGSERIAL   PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source      TEXT,
    payload     JSONB,
    reason      TEXT        NOT NULL
);
CREATE INDEX idx_dead_letter_received_at ON dead_letter (received_at DESC);


-- 4. Lifecycle policies — starter values, tune for the workload.
--    See: "TimescaleDB for Manufacturing IoT: Optimizing for High-Volume
--    Production Data" for chunk interval, compression, and continuous aggregate
--    guidance.
-- SELECT add_compression_policy('tag_history', INTERVAL '7 days');
-- SELECT add_retention_policy('tag_history', INTERVAL '2 years');
