CREATE SCHEMA IF NOT EXISTS qa_support;
CREATE TABLE IF NOT EXISTS qa_support.action_history (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL,
    action text NOT NULL,
    namespace text NOT NULL,
    target text NOT NULL,
    outcome text NOT NULL,
    http_status integer NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS action_history_created_idx
    ON qa_support.action_history (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS action_history_namespace_idx
    ON qa_support.action_history (namespace, created_at DESC);
