"""各业务模块共用的列表、登记与状态流转实现。

16 个业务模块原先各自把 ``list_entries`` / ``get_entry`` / ``create_entry`` /
``run_action`` 抄了一遍。这里收拢成一份共用实现：输入参数、返回结构、状态流转
口径与报错文案都保持原样，子类只声明模块常量即可。
"""
from __future__ import annotations

from typing import Any

from app.store import store


class RuleBasedService:
    """按“必填字段 + 状态序列 + 动作映射”驱动的通用业务服务。"""

    module: str = ""
    required_fields: list[str] = []
    status_order: list[str] = []
    action_rules: dict[str, str] = {}
    negative_actions: list[str] = []
    # 列表检索按首个必填字段（同时也是业务编号）过滤
    entity_label: str = ""
    scope_label: str = ""

    @property
    def keyword_field(self) -> str:
        return self.required_fields[0] if self.required_fields else ""

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(self.module)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(self.keyword_field, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.module, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in self.required_fields if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(self.module)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in self.required_fields})
        entry["status"] = self.status_order[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.module, entry_id)
        if entry is None:
            return None, f"{self.entity_label} {entry_id} 不存在或已归档"
        if action not in self.action_rules:
            return None, f"动作「{action}」不属于{self.scope_label}可执行范围"
        target = self.action_rules[action]
        if target not in self.status_order:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != self.status_order[-1]
        entry["abnormal"] = action in self.negative_actions
        return entry, f"{self.entity_label}已{action}"
