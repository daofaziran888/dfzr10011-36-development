"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

`lock` 给跨模块的读写（存储归档、聚合准入刷新）提供同一把可重入锁：
分层归档结果、迁移记录与聚合准入队列要么一起提交，要么都看不到，
并发读取只会看到已提交的策略版本与层级。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_ROWS

# 归档链路的辅助表：服务于采集存储分层与结果聚合两个模块，
# 不单独作为业务模块出现在运营概览里。
AUXILIARY_MODULES = frozenset({
    "storage_file",
    "storage_archive",
    "storage_cleanup",
    "storage_recovery",
    "storage_migration",
    "storage_scan",
    "merge_storage_admission",
    "merge_storage_archive",
})


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def business_modules(self) -> list[str]:
        return [name for name in self.module_names() if name not in AUXILIARY_MODULES]

    def reset(self) -> None:
        """把内存表恢复成示例数据：测试与本地重放都靠它回到干净状态。"""
        with self.lock:
            self._tables = {
                name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
            }

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        rows = self.rows(module)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.business_modules():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
