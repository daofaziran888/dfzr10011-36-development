"""存储分层规则的共用判定。

归档执行、重建清理清单、历史回填三个入口都要判断“这个文件落到哪一层、
保留期是否准入、过期后该不该清理、恢复引用和清理计划冲突时谁优先”。
这些判断原先在调用方各写一遍，容易写歪；这里收拢成纯函数，
输入是策略、文件和当前优先级，输出是可直接落库的判定结果。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

# 支持入库的文件格式；不在表里的一律判为格式不支持
SUPPORTED_FORMATS = ("parquet", "orc", "json", "avro")
# 空文件单独归到这个展示层级，不参与准入和迁移
EMPTY_FILE_TIER = "空文件"
# 恢复终态与清理终态的优先级写法
RECOVERY_PRECEDENCE = "恢复终态优先"
CLEANUP_PRECEDENCE = "清理终态优先"
PRECEDENCE_OPTIONS = (RECOVERY_PRECEDENCE, CLEANUP_PRECEDENCE)

ADMITTED = "准入"
REJECTED = "拒绝"

ARCHIVE_USABLE = "可用"
ARCHIVE_RETAINED = "恢复保留"
ARCHIVE_CLEANED = "已清理"
ARCHIVE_UNTIERED = "未分层"

CLEANUP_PENDING = "待清理"
CLEANUP_DEFERRED = "已延迟"
CLEANUP_DONE = "已清理"


def today() -> date:
    return date.today()


def parse_date(value: Any) -> date | None:
    """把 ``2026-09-01`` / ``2026-09-01T10:00:00`` 解析成日期，解析不了返回 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def text_param(value: Any) -> str | None:
    """把路由层可能透传下来的 Query 默认值归一成真正的字符串或 None。"""
    return value if isinstance(value, str) and value.strip() else None


def normalize_format(value: Any) -> str:
    return str(value or "").strip().lower()


def file_age_days(file_row: dict[str, Any], reference: date | None = None) -> int | None:
    created = parse_date(file_row.get("创建时间"))
    if created is None:
        return None
    return ((reference or today()) - created).days


@dataclass(frozen=True)
class TierRule:
    """策略版本下的一条层级规则。"""

    tier: str
    policy_id: int
    policy_no: str
    admission_days: int
    retention_days: int
    file_format: str
    migration_rule: str
    cleanup_method: str
    status: str

    @property
    def usable(self) -> bool:
        """只有已生效的层级才能被归档结果引用。"""
        return self.status == "已生效"


def build_tier_rules(policy_rows: Iterable[dict[str, Any]], policy_version: str) -> list[TierRule]:
    """取同一策略版本下的层级规则，按准入天数升序排列。"""
    rules: list[TierRule] = []
    for row in policy_rows:
        if str(row.get("策略版本") or "") != policy_version:
            continue
        rules.append(
            TierRule(
                tier=str(row.get("存储层级") or ""),
                policy_id=parse_int(row.get("id")),
                policy_no=str(row.get("策略编号") or ""),
                admission_days=parse_int(row.get("准入天数"), parse_int(row.get("保留天数"), 0)),
                retention_days=parse_int(row.get("保留天数"), 0),
                file_format=normalize_format(row.get("文件格式")),
                migration_rule=str(row.get("迁移规则") or ""),
                cleanup_method=str(row.get("清理方式") or ""),
                status=str(row.get("status") or row.get("策略状态") or ""),
            )
        )
    return sorted(rules, key=lambda rule: (rule.admission_days, rule.tier))


def select_tier(rules: list[TierRule], age_days: int) -> TierRule | None:
    """按文件年龄选择准入天数刚好覆盖它的最低可用层级。"""
    for rule in rules:
        if rule.usable and age_days <= rule.admission_days:
            return rule
    return None


def retention_due(created: Any, retention_days: int, reference: date | None = None) -> date | None:
    created_date = parse_date(created)
    if created_date is None or retention_days <= 0:
        return None
    return created_date + timedelta(days=retention_days)


def is_expired(created: Any, retention_days: int, reference: date | None = None) -> bool:
    due = retention_due(created, retention_days, reference)
    if due is None:
        return False
    return (reference or today()) > due


def _max_retention(rules: list[TierRule]) -> tuple[int, TierRule | None]:
    if not rules:
        return 0, None
    rule = max(rules, key=lambda item: item.retention_days)
    return rule.retention_days, rule


def decide_tiering(
    file_row: dict[str, Any],
    rules: list[TierRule],
    *,
    reference: date | None = None,
) -> dict[str, Any]:
    """校验文件并给出归档层级、保留期准入和清理依据。

    返回值字段固定，归档执行、重建清理清单和历史回填都按同一份口径读取：
    ``tier`` / ``admitted`` / ``expired`` / ``reason`` / ``cleanup_basis`` /
    ``retention_due``。
    """
    reference = reference or today()
    file_format = normalize_format(file_row.get("文件格式"))
    size = parse_int(file_row.get("文件大小"))
    if size <= 0:
        return {
            "tier": EMPTY_FILE_TIER,
            "target_tier": EMPTY_FILE_TIER,
            "admitted": False,
            "expired": False,
            "reason": "空文件不参与分层准入",
            "cleanup_basis": "空文件待人工确认",
            "retention_due": None,
            "valid": False,
            "policy_no": "",
            "retention_days": 0,
            "age_days": None,
            "empty_file": True,
        }
    if file_format not in SUPPORTED_FORMATS:
        return {
            "tier": ARCHIVE_UNTIERED,
            "target_tier": ARCHIVE_UNTIERED,
            "admitted": False,
            "expired": False,
            "reason": f"文件格式 {file_format or '未填写'} 不在支持范围",
            "cleanup_basis": f"格式不支持（{file_format or '未填写'}）",
            "retention_due": None,
            "valid": False,
            "policy_no": "",
            "retention_days": 0,
            "age_days": None,
            "empty_file": False,
        }
    age = file_age_days(file_row, reference)
    if age is None or age < 0:
        reason = "创建时间缺失，无法计算保留期" if age is None else "创建时间晚于当前日期，无法计算保留期"
        return {
            "tier": ARCHIVE_UNTIERED,
            "target_tier": ARCHIVE_UNTIERED,
            "admitted": False,
            "expired": False,
            "reason": reason,
            "cleanup_basis": f"{reason}，转人工核对",
            "retention_due": None,
            "valid": False,
            "policy_no": "",
            "retention_days": 0,
            "age_days": age,
            "empty_file": False,
        }

    rule = select_tier(rules, age)
    if rule is None:
        # 策略版本里没有可用层级覆盖：按可用层级的最长保留期判断是否过期
        usable_rules = [item for item in rules if item.usable]
        retention, _ = _max_retention(usable_rules)
        due = retention_due(file_row.get("创建时间"), retention, reference)
        expired = is_expired(file_row.get("创建时间"), retention, reference)
        basis = (
            f"超过保留期（可用层级最长保留 {retention} 天，到期 {due.isoformat() if due else '—'}）"
            if expired
            else f"保留期内（可用层级最长保留 {retention} 天，到期 {due.isoformat() if due else '—'}），等待可用层级"
        )
        deepest = max(usable_rules, key=lambda item: item.admission_days) if usable_rules else None
        return {
            "tier": ARCHIVE_UNTIERED,
            "target_tier": deepest.tier if deepest else ARCHIVE_UNTIERED,
            "admitted": False,
            "expired": expired,
            "reason": f"策略版本 {_version_of(rules)} 没有可用层级覆盖 {age} 天年龄",
            "cleanup_basis": basis,
            "retention_due": due.isoformat() if due else None,
            "valid": False,
            "policy_no": "",
            "retention_days": retention,
            "age_days": age,
            "empty_file": False,
        }

    due = retention_due(file_row.get("创建时间"), rule.retention_days, reference)
    expired = is_expired(file_row.get("创建时间"), rule.retention_days, reference)
    return {
        "tier": rule.tier,
        "target_tier": rule.tier,
        "admitted": not expired,
        "expired": expired,
        "reason": (
            f"超过保留期（层级 {rule.tier} 保留 {rule.retention_days} 天）"
            if expired
            else f"按策略 {rule.policy_no} 准入{rule.tier}"
        ),
        "cleanup_basis": (
            f"超过保留期（层级 {rule.tier} 保留 {rule.retention_days} 天，到期 {due.isoformat() if due else '—'}）"
            if expired
            else f"保留期内（层级 {rule.tier} 保留 {rule.retention_days} 天，到期 {due.isoformat() if due else '—'}）"
        ),
        "retention_due": due.isoformat() if due else None,
        "valid": not expired,
        "policy_no": rule.policy_no,
        "retention_days": rule.retention_days,
        "age_days": age,
        "empty_file": False,
    }


def _version_of(rules: list[TierRule]) -> str:
    """仅用于报错文案：策略版本体现在规则来源里，取第一条策略编号前缀即可。"""
    if not rules:
        return "未知"
    return rules[0].policy_no.rsplit("-", 1)[0] if "-" in rules[0].policy_no else rules[0].policy_no


def decide_conflict(has_cleanup: bool, has_recovery: bool, precedence: str) -> str:
    """冲突归属：返回 ``recovery`` / ``cleanup`` / ``none``。

    只有清理计划与恢复检查点同时命中同一份过期文件才算冲突；
    恢复终态优先时保留数据，清理终态优先时允许清除并给恢复入口换层。
    """
    if has_cleanup and has_recovery:
        return "cleanup" if precedence == CLEANUP_PRECEDENCE else "recovery"
    if has_recovery:
        return "recovery"
    if has_cleanup:
        return "cleanup"
    return "none"


def decide_cleanup(
    *,
    file_row: dict[str, Any],
    tiering: dict[str, Any],
    precedence: str,
) -> dict[str, Any]:
    """清理判断：归档状态、清理状态、延迟原因都用这一份结论。

    ``file_row`` 里 ``清理计划`` 表示是否有清理计划，``恢复引用`` 表示断点编号。
    """
    recovery_no = str(file_row.get("恢复引用") or "").strip()
    has_recovery = bool(recovery_no)
    has_cleanup = bool(file_row.get("清理计划"))
    if not tiering.get("expired"):
        return {
            "archive_status": ARCHIVE_USABLE if tiering.get("admitted") else ARCHIVE_UNTIERED,
            "cleanup_status": "",
            "defer_reason": "",
            "cleanup_basis": tiering.get("cleanup_basis", ""),
            "conflict": "none",
            "new_tier_entry": "",
        }
    conflict = decide_conflict(has_cleanup, has_recovery, precedence)
    basis = tiering.get("cleanup_basis", "")
    if conflict == "recovery":
        if has_cleanup:
            reason = f"清理计划与恢复检查点 {recovery_no} 冲突，恢复终态优先，清理延后"
        else:
            reason = f"被恢复检查点 {recovery_no} 引用，不允许删除或改写"
        return {
            "archive_status": ARCHIVE_RETAINED,
            "cleanup_status": CLEANUP_DEFERRED if has_cleanup else "",
            "defer_reason": reason,
            "cleanup_basis": f"{basis}；{reason}",
            "conflict": "recovery",
            "new_tier_entry": "",
        }
    if has_recovery:
        target_tier = tiering.get("target_tier") or tiering.get("tier") or ARCHIVE_UNTIERED
        entry = f"{target_tier}/恢复入口/{file_row.get('文件编号')}"
        basis = f"{basis}；清理终态优先，恢复入口改走新层级"
        return {
            "archive_status": ARCHIVE_CLEANED,
            "cleanup_status": CLEANUP_DONE if has_cleanup else "",
            "defer_reason": "",
            "cleanup_basis": basis,
            "conflict": "cleanup",
            "new_tier_entry": entry,
        }
    return {
        "archive_status": ARCHIVE_CLEANED,
        "cleanup_status": CLEANUP_DONE if has_cleanup else "",
        "defer_reason": "",
        "cleanup_basis": basis,
        "conflict": "cleanup" if has_cleanup else "none",
        "new_tier_entry": "",
    }


def reprioritize_archive(archive_row: dict[str, Any], precedence: str) -> dict[str, Any] | None:
    """清理清单重建时的重复判定：已归档结论只有在清理终态优先时才改判。

    返回需要更新的字段；不需要改判时返回 None。归档执行与清理清单重建
    共用 ``decide_conflict`` / ``decide_cleanup`` 的同一套冲突口径。
    """
    if archive_row.get("归档状态") != ARCHIVE_RETAINED:
        return None
    if not str(archive_row.get("恢复引用") or ""):
        return None
    if str(archive_row.get("冲突处理") or "无冲突") == "无冲突":
        return None
    if precedence != CLEANUP_PRECEDENCE:
        return None
    target_tier = archive_row.get("目标层级") or archive_row.get("归档层级") or ARCHIVE_UNTIERED
    return {
        "归档状态": ARCHIVE_CLEANED,
        "清理状态": CLEANUP_DONE,
        "延迟原因": "",
        "新层级入口": f"{target_tier}/恢复入口/{archive_row.get('文件编号')}",
        "冲突处理": "清理终态优先",
        "清理依据": f"{archive_row.get('清理依据') or ''}；清理终态优先，恢复入口改走新层级",
    }


def backfill_fields(decision: dict[str, Any]) -> dict[str, Any]:
    """回填判定的落库字段：归档层级、依据来源与归档状态按同一口径生成。"""
    tier = decision["tier"]
    status = ARCHIVE_UNTIERED if tier in (EMPTY_FILE_TIER, ARCHIVE_UNTIERED) else ARCHIVE_USABLE
    return {
        "归档层级": tier,
        "分层依据": decision["reason"],
        "分层依据来源": decision.get("basis", "按原层级回填"),
        "归档状态": status,
        "准入结果": ADMITTED if status == ARCHIVE_USABLE else REJECTED,
    }


def decide_backfill(
    archive_row: dict[str, Any],
    files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """历史回填判断：缺少分层依据的记录按文件原层级回填。

    返回 ``ok`` / ``skipped`` / ``tier`` / ``basis`` / ``reason``；
    失败时调用方继续处理后面的记录，不再从头重跑已迁移部分。
    """
    if str(archive_row.get("分层依据") or "").strip() and str(archive_row.get("归档层级") or "").strip():
        return {"ok": False, "skipped": True, "reason": "已有分层依据，无需回填"}
    file_no = str(archive_row.get("文件编号") or "").strip()
    file_row = files.get(file_no)
    if file_row is None:
        return {"ok": False, "skipped": False, "reason": f"文件 {file_no} 不存在，回填失败"}
    original_tier = str(file_row.get("原存储层级") or "").strip()
    if not original_tier:
        return {"ok": False, "skipped": False, "reason": f"文件 {file_no} 缺少原存储层级，回填失败"}
    return {
        "ok": True,
        "skipped": False,
        "tier": original_tier,
        "basis": "按原层级回填",
        "reason": f"按文件原层级回填 {original_tier}",
    }
