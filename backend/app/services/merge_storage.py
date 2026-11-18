"""聚合模块的存储分层读侧：新增准入队列、归档结果快照与按策略版本引用。

归档链路写出的分层归档结果会落到 ``merge_storage_admission`` 待处理队列，
同时在 ``merge_storage_archive`` 留一份聚合侧快照。结果聚合只从快照里读取
“同一策略版本 + 层级仍可用”的记录，越权层级（暂停、关闭、别的版本）一律不引用。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.storage_policy import build_tier_rules, parse_int, text_param
from app.store import store

QUEUE_MODULE = "merge_storage_admission"
SNAPSHOT_MODULE = "merge_storage_archive"
POLICY_MODULE = "storage"

QUEUE_PENDING = "待处理"
QUEUE_REVIEW = "待复核"


def version_key(version: str) -> tuple[int, ...]:
    """把 ``v2`` / ``2026.09`` 这类版本号变成可排序的整数序列。"""
    digits = "".join(char if char.isdigit() else " " for char in str(version)).split()
    return tuple(int(part) for part in digits) or (0,)


def policy_versions() -> list[str]:
    rows = store.rows(POLICY_MODULE)
    return sorted({str(row.get("策略版本") or "") for row in rows if row.get("策略版本")}, key=version_key)


def latest_version() -> str:
    versions = policy_versions()
    return versions[-1] if versions else ""


def usable_tiers(policy_version: str) -> set[str]:
    """同一策略版本下真正可用的层级名（仅已生效）。"""
    rules = build_tier_rules(store.rows(POLICY_MODULE), policy_version)
    return {rule.tier for rule in rules if rule.usable}


def archive_rows(policy_version: str | None = None) -> list[dict[str, Any]]:
    rows = store.rows("storage_archive")
    policy_version = text_param(policy_version)
    if policy_version:
        rows = [row for row in rows if str(row.get("策略版本") or "") == policy_version]
    return rows


def _queue_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("归档编号") or ""), str(row.get("策略版本") or "")


def refresh(policy_version: str | None = None) -> dict[str, Any]:
    """同步刷新聚合侧的两份数据：归档结果快照 + 新增准入清单。

    指定策略版本时只刷新该版本，其余版本保持已提交的层级不动；
    不指定版本时刷新所有已提交归档结果。调用方可以持锁，这里也独立加锁，
    重复调用结果一致。
    """
    policy_version = text_param(policy_version)
    with store.lock:
        version = policy_version or latest_version()
        if policy_version:
            versions = {version}
            archives = [
                dict(row) for row in store.rows("storage_archive")
                if str(row.get("策略版本") or "") == version
            ]
            snapshot = [
                dict(row) for row in store.rows(SNAPSHOT_MODULE)
                if str(row.get("策略版本") or "") != version
            ] + archives
        else:
            versions = {
                str(row.get("策略版本") or "") for row in store.rows("storage_archive")
            }
            archives = [dict(row) for row in store.rows("storage_archive")]
            snapshot = [dict(row) for row in archives]
        # 快照保留归档结果原字段，聚合侧按策略版本核对层级与清理依据
        store.rows(SNAPSHOT_MODULE)[:] = snapshot

        existing = {
            _queue_key(row): row
            for row in store.rows(QUEUE_MODULE)
            if str(row.get("策略版本") or "") in versions
        }
        preserved = [
            dict(row) for row in store.rows(QUEUE_MODULE)
            if str(row.get("策略版本") or "") not in versions
        ]
        next_id = max((parse_int(row.get("id")) for row in store.rows(QUEUE_MODULE)), default=0) + 1
        queued: list[dict[str, Any]] = list(preserved)
        admitted = review = 0
        for row in archives:
            tier_usable = (
                str(row.get("归档层级") or "") in usable_tiers(str(row.get("策略版本") or ""))
                and row.get("归档状态") == "可用"
            )
            status = QUEUE_PENDING if tier_usable else QUEUE_REVIEW
            if tier_usable:
                admitted += 1
            else:
                review += 1
            key = _queue_key(row)
            entry = existing.get(key)
            if entry is None:
                entry = {
                    "id": next_id,
                    "准入编号": f"ADMS-{next_id:04d}",
                    "加入时间": date.today().isoformat(),
                    "pending": True,
                    "abnormal": not tier_usable,
                }
                next_id += 1
            entry.update({
                "归档编号": row.get("归档编号"),
                "文件编号": row.get("文件编号"),
                "文件名": row.get("文件名"),
                "策略版本": row.get("策略版本"),
                "策略编号": row.get("策略编号"),
                "归档层级": row.get("归档层级"),
                "准入结果": row.get("准入结果"),
                "归档状态": row.get("归档状态"),
                "保留天数": row.get("保留天数"),
                "到期日期": row.get("到期日期"),
                "清理依据": row.get("清理依据"),
                "恢复引用": row.get("恢复引用"),
                "状态": status,
                "pending": status == QUEUE_PENDING,
                "abnormal": not tier_usable,
                "来源": "分层归档结果",
            })
            queued.append(entry)
        store.rows(QUEUE_MODULE)[:] = queued
        return {
            "policy_version": version,
            "versions": sorted(versions, key=version_key),
            "archive_total": len(archives),
            "queue_total": len(queued),
            "admitted": admitted,
            "review": review,
            "refreshed_at": date.today().isoformat(),
        }


def list_admission(
    *,
    policy_version: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    policy_version = text_param(policy_version)
    status = text_param(status)
    with store.lock:
        rows = [dict(row) for row in store.rows(QUEUE_MODULE)]
    if policy_version:
        rows = [row for row in rows if str(row.get("策略版本") or "") == policy_version]
    if status:
        rows = [row for row in rows if row.get("状态") == status]
    total = len(rows)
    start = max(page - 1, 0) * size
    return rows[start:start + size], total


def reference(policy_version: str | None = None) -> dict[str, Any]:
    """结果聚合读取分层归档结果：只引用同一策略版本下的可用层级。"""
    policy_version = text_param(policy_version)
    with store.lock:
        version = policy_version or latest_version()
        snapshot = [dict(row) for row in store.rows(SNAPSHOT_MODULE)]
        if not snapshot:
            snapshot = [dict(row) for row in store.rows("storage_archive")]
    usable = usable_tiers(version)
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in snapshot:
        row_version = str(row.get("策略版本") or "")
        row_tier = str(row.get("归档层级") or "")
        if row_version != version:
            excluded.append({"归档编号": row.get("归档编号"), "原因": f"策略版本 {row_version} 与引用版本 {version} 不一致"})
            continue
        if row_tier not in usable:
            excluded.append({"归档编号": row.get("归档编号"), "原因": f"层级 {row_tier} 在策略版本 {version} 下不可用"})
            continue
        if row.get("归档状态") != "可用":
            excluded.append({"归档编号": row.get("归档编号"), "原因": f"归档状态为{row.get('归档状态') or '未分层'}"})
            continue
        items.append(row)

    tiers: dict[str, dict[str, Any]] = {}
    cleanup_basis: dict[str, int] = {}
    for item in items:
        tier = str(item.get("归档层级") or "")
        bucket = tiers.setdefault(tier, {"归档层级": tier, "数量": 0, "准入结果": item.get("准入结果"), "策略编号": item.get("策略编号")})
        bucket["数量"] += 1
        basis = str(item.get("清理依据") or "")
        if basis:
            cleanup_basis[basis] = cleanup_basis.get(basis, 0) + 1
    return {
        "policy_version": version,
        "usable_tiers": sorted(usable),
        "items": items,
        "total": len(items),
        "tiers": sorted(tiers.values(), key=lambda item: item["归档层级"]),
        "cleanup_basis": [
            {"清理依据": basis, "数量": count}
            for basis, count in sorted(cleanup_basis.items())
        ],
        "excluded": excluded,
    }
