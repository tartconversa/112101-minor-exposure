"""青少年保护暴露窗口合成数仓。

仅处理本地合成数据（SYN-N 虚拟主体标识），不包含真实个人资料，
不访问网络或任何外部服务，仅依赖 Python 标准库。
"""

from __future__ import annotations

__all__ = ["domain", "pipeline", "reporting", "warehouse"]
