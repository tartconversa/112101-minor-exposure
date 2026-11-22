"""SQLite 数仓的分层 schema。

分层：
- raw_*   原始业务记录（保护策略 / 年龄信号 / 分发事件），只做类型化落地。
- dim_*   维度表（入口维度由合成数据约定生成）。
- stg_*   每次报告批次的快照：只包含截止时间前已到达（ingest_at <= cutoff）的记录。
- fact_*  暴露窗口事实：逐条分发事件的年龄判定、策略匹配与保护桶。
- report_* 报告批次、按维度的汇总、批次间重述（restatement）差异。
"""

from __future__ import annotations

DDL = """
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- raw 层
CREATE TABLE IF NOT EXISTS raw_protection_rules (
    record_id       TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    age_band        TEXT NOT NULL,
    region          TEXT NOT NULL,
    channel         TEXT NOT NULL,
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    rule_version    TEXT NOT NULL,
    ingest_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_age_signals (
    record_id     TEXT PRIMARY KEY,
    signal_id     TEXT NOT NULL,
    subject_key   TEXT NOT NULL,
    age_band      TEXT,
    observed_at   TEXT NOT NULL,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    signal_state  TEXT NOT NULL,
    region        TEXT NOT NULL,
    ingest_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_distributions (
    record_id          TEXT PRIMARY KEY,
    distribution_id    TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    subject_key        TEXT NOT NULL,
    channel            TEXT NOT NULL,
    occurred_at        TEXT NOT NULL,
    restriction_state  TEXT NOT NULL,
    event_ref          TEXT NOT NULL,
    region             TEXT NOT NULL,
    ingest_at          TEXT NOT NULL
);

-- ---------------------------------------------------------------- 维度层
CREATE TABLE IF NOT EXISTS dim_entry (
    channel       TEXT PRIMARY KEY,
    entry_name    TEXT NOT NULL,
    youth_path    INTEGER NOT NULL  -- 1 = 青少年可能进入的入口
);

-- ------------------------------------------------ 报告批次与快照（staging 层）
CREATE TABLE IF NOT EXISTS report_runs (
    run_id        TEXT PRIMARY KEY,
    cutoff_at     TEXT NOT NULL,    -- 数据可见性截止（记录时间口径）
    window_start  TEXT NOT NULL,    -- 事件时间窗口起点
    window_end    TEXT NOT NULL,    -- 事件时间窗口终点（开区间）
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_rules (
    run_id          TEXT NOT NULL REFERENCES report_runs(run_id),
    record_id       TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    age_band        TEXT NOT NULL,
    region          TEXT NOT NULL,
    channel         TEXT NOT NULL,
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    rule_version    TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);

CREATE TABLE IF NOT EXISTS stg_age_signals (
    run_id        TEXT NOT NULL REFERENCES report_runs(run_id),
    record_id     TEXT NOT NULL,
    signal_id     TEXT NOT NULL,
    subject_key   TEXT NOT NULL,
    age_band      TEXT,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    signal_state  TEXT NOT NULL,
    region        TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);

CREATE TABLE IF NOT EXISTS stg_distributions (
    run_id             TEXT NOT NULL REFERENCES report_runs(run_id),
    record_id          TEXT NOT NULL,
    distribution_id    TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    subject_key       TEXT NOT NULL,
    channel            TEXT NOT NULL,
    occurred_at        TEXT NOT NULL,
    restriction_state  TEXT NOT NULL,
    event_ref          TEXT NOT NULL,
    region             TEXT NOT NULL,
    PRIMARY KEY (run_id, record_id)
);

-- ---------------------------------------------------------------- 事实层
-- 每个 (run_id, event_ref) 一行：同一条分发事件在不同批次可能因新信号、
-- 迟到策略而得到不同判定，全部保留以便重述追踪。
CREATE TABLE IF NOT EXISTS fact_exposure (
    run_id               TEXT NOT NULL REFERENCES report_runs(run_id),
    event_ref            TEXT NOT NULL,
    distribution_id      TEXT NOT NULL,
    content_id           TEXT NOT NULL,
    subject_key          TEXT NOT NULL,
    channel              TEXT NOT NULL,
    region               TEXT NOT NULL,
    occurred_at          TEXT NOT NULL,
    restriction_state    TEXT NOT NULL,
    age_assessment       TEXT NOT NULL,  -- minor_confirmed / adult_confirmed / unknown
    age_band             TEXT,
    age_reason           TEXT NOT NULL,  -- ok / missing / expired / pending_correction / disputed
    signal_record_id     TEXT,
    matched_rule_id      TEXT,
    matched_rule_version TEXT,
    rule_status          TEXT,           -- active / withdrawn / no_match
    restriction_applied  INTEGER NOT NULL,
    exposure_bucket      TEXT NOT NULL,
    exposure_reason      TEXT,
    PRIMARY KEY (run_id, event_ref)
);

CREATE INDEX IF NOT EXISTS idx_fact_run_bucket
    ON fact_exposure(run_id, exposure_bucket);
CREATE INDEX IF NOT EXISTS idx_fact_run_rule
    ON fact_exposure(run_id, matched_rule_version);
CREATE INDEX IF NOT EXISTS idx_fact_run_window
    ON fact_exposure(run_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_fact_run_entry
    ON fact_exposure(run_id, channel);

-- ---------------------------------------------------------------- 汇总与重述
CREATE TABLE IF NOT EXISTS report_summary (
    run_id           TEXT NOT NULL REFERENCES report_runs(run_id),
    rule_version     TEXT NOT NULL,      -- 'NONE' 表示无适用策略
    channel          TEXT NOT NULL,      -- 'ALL' 表示全部入口
    region           TEXT NOT NULL,      -- 'ALL' 表示全部地区
    exposure_bucket  TEXT NOT NULL,
    event_count      INTEGER NOT NULL,
    PRIMARY KEY (run_id, rule_version, channel, region, exposure_bucket)
);

CREATE TABLE IF NOT EXISTS report_restatements (
    run_id              TEXT NOT NULL,
    prior_run_id        TEXT NOT NULL,
    event_ref           TEXT NOT NULL,
    prior_bucket        TEXT NOT NULL,
    current_bucket      TEXT NOT NULL,
    prior_rule_version  TEXT,
    current_rule_version TEXT,
    change_driver       TEXT NOT NULL,   -- age_signal_change / policy_change / new_event
    change_detail       TEXT NOT NULL,
    PRIMARY KEY (run_id, prior_run_id, event_ref)
);
"""

# 面向复核者的只读视图。
VIEWS = """
CREATE VIEW IF NOT EXISTS v_confirmed_protection AS
SELECT run_id, event_ref, content_id, subject_key, channel, region,
       occurred_at, matched_rule_id, matched_rule_version, restriction_state
FROM fact_exposure
WHERE exposure_bucket IN ('PROTECTED_MINOR', 'UNKNOWN_AGE_PROTECTED');

CREATE VIEW IF NOT EXISTS v_minor_exposure AS
SELECT run_id, event_ref, content_id, subject_key, channel, region,
       occurred_at, exposure_reason, matched_rule_id, matched_rule_version,
       rule_status, restriction_state
FROM fact_exposure
WHERE exposure_bucket = 'MINOR_EXPOSURE';

CREATE VIEW IF NOT EXISTS v_insufficient_data AS
SELECT run_id, event_ref, content_id, subject_key, channel, region,
       occurred_at, age_reason, matched_rule_id, matched_rule_version,
       restriction_state, exposure_reason
FROM fact_exposure
WHERE exposure_bucket IN ('UNKNOWN_AGE_EXPOSED', 'UNKNOWN_AGE_PROTECTED');
"""
