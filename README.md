# 青少年内容保护暴露窗口数仓（合成数据）

面向未成年人保护团队与产品运营团队的可复核数仓：用**合成的**保护策略、年龄
状态与分发事件，说明特定内容在保护措施调整前后可能进入哪些青少年使用场景、
措施何时生效，以及仍有哪些暴露无法确认。

- 仅使用 Python 标准库与内置 SQLite，无需联网、无需第三方包。
- 只有本地合成数据：主体标识一律为 `SYN-N` 虚拟标识，无真实个人资料。
- 判定口径见 [`docs/data_model.md`](docs/data_model.md)。

## 目录

- `sources/` 原始合成业务记录（UTF-8 CSV，时间为带时区 ISO 8601；年龄信号
  的有效期为日期口径，按 UTC 零点解释）。
  - `protection_rules.csv`：保护策略团队维护的规则——年龄段、地区、入口、
    有效时间、规则版本、记录到达时间（含一条迟到补传规则）。
  - `age_signals.csv`：设备/账号年龄信号——verified、corrected、unknown、
    pending_correction、disputed，含有效期与记录到达时间。
  - `distributions.csv`：运营团队的分发事实——入口、事件时间、限制状态
    （limited/blocked/served/reviewed/unknown）、记录到达时间。
- `src/protected_exposure/` 数仓包：
  - `time_utils.py` 时间解析；`domain.py` 年龄判定/策略匹配/保护桶（纯函数）；
    `warehouse.py` raw 落地；`pipeline.py` 批次流水线与重述；
    `reporting.py` 复核查询；`cli.py` 命令行。
- `tests/` 39 个单元/集成测试（含原始基线测试）。
- `docs/data_model.md` 判定口径与表结构。

## 快速开始

```bash
# 构建并执行三个演示报告批次（数据库生成在 work/warehouse.sqlite）
python -m protected_exposure.cli run-demo   # 若未安装包，见下方"运行方式"

# 列出报告批次
python -m protected_exposure.cli runs

# 按保护桶查看某批次
python -m protected_exposure.cli buckets RUN-2026-05-15

# 规则版本 × 入口 × 桶 汇总
python -m protected_exposure.cli summary RUN-2026-05-15

# 逐条事实，支持按规则版本/入口/时间窗口/桶过滤
python -m protected_exposure.cli facts RUN-2026-05-15 --bucket MINOR_EXPOSURE --channel feed
python -m protected_exposure.cli facts RUN-2026-05-15 --rule-version R-6
python -m protected_exposure.cli facts RUN-2026-05-15 \
  --start 2026-04-01T00:00:00Z --end 2026-04-20T00:00:00Z

# 相对上一批次的重述差异（年龄信号/策略变更对先前报告的影响）
python -m protected_exposure.cli restatements RUN-2026-05-15
```

未安装包时用显式路径运行（隔离模式 `-I`，不依赖当前目录）：

```bash
python -I -c "import sys; sys.path.insert(0, 'src'); \
  from protected_exposure.cli import main; sys.exit(main(['run-demo']))"
```

## 运行测试

```bash
python -m unittest discover -s tests -v
# 隔离模式：
python -I -m unittest discover -s tests -v
```

## 演示场景覆盖

三个批次的截止时间分别为 2026-03-15、2026-05-01、2026-05-15，刻意构造了：

- **措施生效前**的已确认未成年人曝光（`pre_policy`，EV-4）；
- **策略撤回后**的后续曝光（`post_withdrawal`，EV-13/EV-24）；
- 入口/地区不在策略范围（`entry_uncovered`，EV-16）与策略适用但分发未限制
  （`restriction_gap`，EV-3/EV-19/EV-23）；
- 年龄标签**缺失、过期、纠正中、争议**四类无法确认人群，独立成桶，
  既不计零也不计为准确未成年人（EV-2/EV-5/EV-17/EV-15 等）；
- **迟到补传策略**（PR-6/R-6）在第三批次改变先前报告（EV-25）；
- **迟到补传的纠正年龄信号**把先前"未知"重述为"已确认未成年人"（EV-19）；
- 迟到补传事件标记为 `new_event`（EV-21 等）；
- 成人对照记录（EV-10），不混入未成年人指标。
