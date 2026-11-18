"""结果聚合归并业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import merge_storage
from app.services.base import RuleBasedService

MODULE = "merge"
REQUIRED_FIELDS = ["任务编号", "采集计划", "分片总数"]
STATUS_ORDER = ["待归并", "归并中", "已完成", "归并失败"]
ACTION_RULES = {"启动归并": "待归并", "重试归并": "归并中", "归档任务": "归并失败"}
NEGATIVE_ACTIONS = []


class MergeService(RuleBasedService):
    module = MODULE
    required_fields = REQUIRED_FIELDS
    status_order = STATUS_ORDER
    action_rules = ACTION_RULES
    negative_actions = NEGATIVE_ACTIONS
    entity_label = "聚合任务"
    scope_label = "结果聚合归并"

    # ---- 存储分层读侧 ---------------------------------------------

    def list_storage_admission(
        self,
        *,
        policy_version: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        return merge_storage.list_admission(
            policy_version=policy_version, status=status, page=page, size=size
        )

    def reference_storage_archive(self, policy_version: str | None = None) -> dict[str, Any]:
        return merge_storage.reference(policy_version)

    def sync_storage_admission(self, policy_version: str | None = None) -> dict[str, Any]:
        return merge_storage.refresh(policy_version)
