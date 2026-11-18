"""采集日志解析业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from app.services.base import RuleBasedService

MODULE = "logparse"
REQUIRED_FIELDS = ["模板编号", "日志类型", "解析规则"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已关闭"]
ACTION_RULES = {"启用模板": "草稿", "暂停模板": "已启用", "关闭模板": "已暂停"}
NEGATIVE_ACTIONS = []


class LogparseService(RuleBasedService):
    module = MODULE
    required_fields = REQUIRED_FIELDS
    status_order = STATUS_ORDER
    action_rules = ACTION_RULES
    negative_actions = NEGATIVE_ACTIONS
    entity_label = "日志模板"
    scope_label = "采集日志解析"
