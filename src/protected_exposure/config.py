"""项目级常量与合成数据约定。

本项目只处理本地合成数据：subject_key 均为 SYN-N 形式的虚拟标识，
不包含真实个人资料，也不访问任何外部服务。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "sources"
DEFAULT_DB_PATH = ROOT / "work" / "warehouse.sqlite"

# 合成演示的报告批次（as-of 截止时间）。
# run-1 / run-2 构成可复核的重述对比；run-3 再纳入迟到策略与争议信号。
DEMO_RUNS = (
    ("RUN-2026-03-15", "2026-03-15T00:00:00Z", "2026-01-01T00:00:00Z", "2026-03-15T00:00:00Z"),
    ("RUN-2026-05-01", "2026-05-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z"),
    ("RUN-2026-05-15", "2026-05-15T00:00:00Z", "2026-01-01T00:00:00Z", "2026-05-15T00:00:00Z"),
)

# 视为"已实施保护动作"的分发限制状态。其余状态（served/reviewed/unknown）
# 表示没有可确认的限制动作。
RESTRICTING_STATES = frozenset({"limited", "blocked"})

# 保护桶定义。任何分发明细最终且仅落入其中一个桶。
BUCKETS = (
    ("PROTECTED_MINOR", "已确认未成年人，且分发记录显示限制动作"),
    ("MINOR_EXPOSURE", "已确认未成年人，未发现可确认的限制动作（含策略缺口）"),
    ("UNKNOWN_AGE_PROTECTED", "年龄无法确认，但分发记录显示限制动作（保守保护）"),
    ("UNKNOWN_AGE_EXPOSED", "年龄无法确认，且无限制动作；不得计为零也不得计为准确未成年人"),
    ("ADULT_REFERENCE", "已确认成年人的对照记录，不计入未成年人暴露"),
)

BUCKET_ORDER = tuple(code for code, _ in BUCKETS)

# 未成年人暴露的细分原因。
EXPOSURE_REASONS = (
    "pre_policy",          # 事件发生在任何适用策略生效之前
    "post_withdrawal",     # 事件发生在适用策略撤回之后
    "entry_uncovered",     # 该入口/地区不在任何适用策略范围内
    "restriction_gap",     # 策略适用，但分发事实没有限制动作
    "no_applicable_rule",  # 不存在任何适用规则
)
