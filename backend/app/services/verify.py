"""采集结果校验业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from app.services.base import RuleBasedService

MODULE = "verify"
REQUIRED_FIELDS = ["规则编号", "采集计划", "校验类型"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已关闭"]
ACTION_RULES = {"启用校验": "草稿", "暂停校验": "已启用", "关闭校验": "已暂停"}
NEGATIVE_ACTIONS = []


class VerifyService(RuleBasedService):
    module = MODULE
    required_fields = REQUIRED_FIELDS
    status_order = STATUS_ORDER
    action_rules = ACTION_RULES
    negative_actions = NEGATIVE_ACTIONS
    entity_label = "校验规则"
    scope_label = "采集结果校验"
