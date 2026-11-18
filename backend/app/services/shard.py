"""任务分片调度业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from app.services.base import RuleBasedService

MODULE = "shard"
REQUIRED_FIELDS = ["分片编号", "采集计划", "分片范围"]
STATUS_ORDER = ["待分配", "执行中", "已完成", "执行失败"]
ACTION_RULES = {"分配节点": "待分配", "重试分片": "执行中", "标记完成": "执行失败"}
NEGATIVE_ACTIONS = []


class ShardService(RuleBasedService):
    module = MODULE
    required_fields = REQUIRED_FIELDS
    status_order = STATUS_ORDER
    action_rules = ACTION_RULES
    negative_actions = NEGATIVE_ACTIONS
    entity_label = "分片任务"
    scope_label = "任务分片调度"
