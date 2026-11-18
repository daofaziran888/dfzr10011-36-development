"""节点状态巡检业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from app.services.base import RuleBasedService

MODULE = "nodecheck"
REQUIRED_FIELDS = ["记录编号", "节点编号", "CPU使用率"]
STATUS_ORDER = ["正常", "亚健康", "异常", "已下线"]
ACTION_RULES = {"标记异常": "亚健康", "隔离节点": "异常", "恢复节点": "已下线"}
NEGATIVE_ACTIONS = []


class NodecheckService(RuleBasedService):
    module = MODULE
    required_fields = REQUIRED_FIELDS
    status_order = STATUS_ORDER
    action_rules = ACTION_RULES
    negative_actions = NEGATIVE_ACTIONS
    entity_label = "巡检记录"
    scope_label = "节点状态巡检"
