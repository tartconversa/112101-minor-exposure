# 数据模型与判定规则（可复核口径）

本文件固定保护团队与运营团队共同使用的判定口径。所有数据均为本地合成数据，
主体标识只有 `SYN-N` 形式的最小虚拟标识，不含真实个人资料，不访问外部服务。

## 1. 三条时间线与迟到数据

每条记录同时具有两个时间：

| 时间 | 含义 | 用途 |
| --- | --- | --- |
| 事件/有效期时间 | `occurred_at`、`effective_from/to`、`valid_from/to`、`observed_at` | 业务事实发生的时间，决定"当时适用什么" |
| 记录时间 | `ingest_at` | 记录进入数仓的时间，决定"某个报告批次能否看到它" |

报告批次（report run）固定一个 **as-of 截止时间 cutoff**：
只有 `ingest_at <= cutoff` 的记录对该批次可见。迟到补传的策略、信号或事件
不会回写历史批次，而是在新批次中通过重述（restatement）体现其影响。

## 2. 年龄信号判定（`domain.assess_age`）

按**事件发生时点**选择信号：信号的 `[valid_from, valid_to)` 必须覆盖该时点；
多条同时覆盖时取 `observed_at` 最新的一条（用户纠正覆盖旧标签）。

| 信号情况 | 判定结果 | age_reason |
| --- | --- | --- |
| `verified`/`corrected` 且年龄段上限 < 18 | `minor_confirmed` | `ok` |
| `verified`/`corrected` 且年龄段上限 >= 18 | `adult_confirmed` | `ok` |
| `signal_state = unknown` | `unknown` | `missing` |
| `signal_state = pending_correction`（用户主动纠正中） | `unknown` | `pending_correction` |
| `signal_state = disputed`（标签被争议） | `unknown` | `disputed` |
| 事件时点没有任何有效信号、但主体曾有信号（过期） | `unknown` | `expired` |
| 主体从未出现过信号（缺失） | `unknown` | `missing` |

**红线：** 缺失、过期、纠正中、争议的标签一律 `unknown`，绝不默认按成人处理。

## 3. 保护策略匹配（`domain.match_rule`）

规则匹配条件（全部满足）：地区一致、入口（channel）一致、规则年龄段覆盖主体
年龄段、事件时点落在规则 `[effective_from, effective_to)` 内。多条生效规则
同时命中时，年龄段最窄（最具体）优先，其次取更高规则版本。

规则状态与暴露原因：

| rule_status | 含义 | exposure_reason |
| --- | --- | --- |
| `active` | 事件时点规则生效 | （无限制动作时为 `restriction_gap`） |
| `not_yet_effective` | 适用规则存在但事件早于其生效时间 | `pre_policy` |
| `withdrawn` | 适用规则在事件时点前已撤回 | `post_withdrawal` |
| `no_match` | 同入口+地区存在其他年龄段规则 | `entry_uncovered` |
| `no_match` | 该入口/地区完全没有规则 | `no_applicable_rule` |

年龄未知时不按年龄段过滤规则，保留同入口/地区的全部规则作为上下文，
避免把未知年龄人群静默排除在策略评估之外。

## 4. 保护桶（每条事件唯一归属）

限制动作以分发事实为准：`restriction_state ∈ {limited, blocked}` 才算
`restriction_applied = 1`；`served`/`reviewed`/`unknown` 均不算。

| 保护桶 | 条件 | 管理含义 |
| --- | --- | --- |
| `PROTECTED_MINOR` | 已确认未成年人 + 有限制动作 | 已确认的保护动作 |
| `MINOR_EXPOSURE` | 已确认未成年人 + 无限制动作 | 已确认暴露，附暴露原因 |
| `UNKNOWN_AGE_PROTECTED` | 年龄无法确认 + 有限制动作 | 保守保护，不能计为准确未成年人 |
| `UNKNOWN_AGE_EXPOSED` | 年龄无法确认 + 无限制动作 | 无法确认的暴露，**不能计为零，也不能计为准确未成年人** |
| `ADULT_REFERENCE` | 已确认成年人 | 对照组，不计入未成年人指标 |

## 5. 报告批次与重述追踪

- `report_runs`：批次头（cutoff、事件时间窗口）。
- `stg_*`：该批次可见记录的不可变快照。
- `fact_exposure`：逐条事件在该批次下的完整判定（同一事件在不同批次各留一行）。
- `report_summary`：按 规则版本 × 入口 × 地区 × 桶 的汇总（含 `ALL` 汇总行）。
- `report_restatements`：相邻批次逐条对比，变更驱动为
  `age_signal_change`（年龄信号变化）、`policy_change`（策略上下文变化，
  含迟到策略）、`new_event`（迟到补传事件）。

复核者可按规则版本、事件时间窗口、入口下钻，并通过重述表追踪先前报告
为何变化。汇总表与事实表通过对账测试保持一致。

## 6. 分层表清单

```
raw_protection_rules / raw_age_signals / raw_distributions   原始类型化落地
dim_entry                                                     入口维度
report_runs / stg_rules / stg_age_signals / stg_distributions 批次与快照
fact_exposure                                                 暴露窗口事实
report_summary / report_restatements                          汇总与重述
v_confirmed_protection / v_minor_exposure / v_insufficient_data  复核视图
```
