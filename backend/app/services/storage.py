"""采集存储分层业务规则：策略维护 + 分层归档执行。

归档一次只使用一个策略版本；校验存储文件格式、按策略计算保留期准入，
把结果写到 ``storage_archive``，并同步刷新聚合模块的新增准入队列与归档结果快照。
被恢复检查点引用的文件不删除、不改写内容，只调整归档结论。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.base import RuleBasedService
from app.services import merge_storage, storage_policy
from app.services.storage_policy import (
    ADMITTED,
    ARCHIVE_CLEANED,
    ARCHIVE_RETAINED,
    ARCHIVE_UNTIERED,
    ARCHIVE_USABLE,
    CLEANUP_DEFERRED,
    CLEANUP_DONE,
    EMPTY_FILE_TIER,
    PRECEDENCE_OPTIONS,
    RECOVERY_PRECEDENCE,
    REJECTED,
    text_param,
)
from app.store import store

MODULE = "storage"
REQUIRED_FIELDS = ["策略编号", "存储层级", "文件格式"]
STATUS_ORDER = ["草稿", "已生效", "已暂停", "已关闭"]
ACTION_RULES = {"启用策略": "草稿", "暂停策略": "已生效", "关闭策略": "已暂停"}
NEGATIVE_ACTIONS = []

FILE_MODULE = "storage_file"
ARCHIVE_MODULE = "storage_archive"
CLEANUP_MODULE = "storage_cleanup"
RECOVERY_MODULE = "storage_recovery"
MIGRATION_MODULE = "storage_migration"

RECOVERY_NEW_ENTRY = "新层级入口"
RECOVERY_KEEP = "保持原层级"


class StorageService(RuleBasedService):
    module = MODULE
    required_fields = REQUIRED_FIELDS
    status_order = STATUS_ORDER
    action_rules = ACTION_RULES
    negative_actions = NEGATIVE_ACTIONS
    entity_label = "存储策略"
    scope_label = "采集存储分层"

    # ---- 归档执行 -------------------------------------------------

    def run_archive(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """执行一次分层归档：一次只使用一个策略版本。"""
        policy_version = text_param(values.get("策略版本")) or ""
        precedence = str(values.get("终态优先级") or RECOVERY_PRECEDENCE).strip()
        with store.lock:
            if not policy_version:
                policy_version = merge_storage.latest_version()
            if not policy_version:
                return None, "没有可用的存储策略版本，无法执行分层归档"
            if precedence not in PRECEDENCE_OPTIONS:
                return None, f"终态优先级「{precedence}」不在支持范围：{'、'.join(PRECEDENCE_OPTIONS)}"
            rules = storage_policy.build_tier_rules(store.rows(MODULE), policy_version)
            if not rules:
                return None, f"策略版本 {policy_version} 下没有分层规则，无法执行分层归档"
            file_ids = values.get("文件编号") or []
            if isinstance(file_ids, str):
                file_ids = [file_ids]
            files = self._select_files(file_ids)
            if not files:
                return None, "没有可归档的存储文件，请先扫描存储文件"
            results = [self._archive_file(file_row, rules, policy_version, precedence) for file_row in files]
            self._sync_lists(policy_version, precedence)
            summary = self._summarize(results)
            summary["policy_version"] = policy_version
            merge_storage.refresh(policy_version)
        return summary, (
            f"策略版本 {policy_version} 分层归档完成：{len(results)} 个文件，"
            f"准入 {summary['admitted']}，清理 {summary['cleaned']}，保留 {summary['retained']}"
        )

    def _select_files(self, file_ids: list[Any]) -> list[dict[str, Any]]:
        rows = store.rows(FILE_MODULE)
        if not file_ids:
            return list(rows)
        wanted = {str(item).strip() for item in file_ids if str(item).strip()}
        return [row for row in rows if str(row.get("文件编号") or "") in wanted]

    def _archive_file(
        self,
        file_row: dict[str, Any],
        rules: list[storage_policy.TierRule],
        policy_version: str,
        precedence: str,
    ) -> dict[str, Any]:
        tiering = storage_policy.decide_tiering(file_row, rules)
        cleanup = storage_policy.decide_cleanup(
            file_row=file_row,
            tiering=tiering,
            precedence=precedence,
        )
        record = {
            "归档编号": self._archive_no(file_row, policy_version),
            "文件编号": file_row.get("文件编号"),
            "文件名": file_row.get("文件名"),
            "策略版本": policy_version,
            "策略编号": tiering.get("policy_no") or (rules[0].policy_no if rules else ""),
            "归档层级": tiering["tier"],
            "目标层级": tiering["target_tier"],
            "保留天数": tiering["retention_days"],
            "到期日期": tiering["retention_due"] or "",
            "准入结果": ADMITTED if tiering["admitted"] else REJECTED,
            "归档状态": cleanup["archive_status"],
            "分层依据": tiering["reason"],
            "清理依据": cleanup["cleanup_basis"],
            "恢复引用": str(file_row.get("恢复引用") or ""),
            "恢复终态": self._recovery_state(file_row),
            "冲突处理": self._conflict_label(cleanup["conflict"]),
            "清理状态": cleanup["cleanup_status"],
            "延迟原因": cleanup["defer_reason"],
            "新层级入口": cleanup["new_tier_entry"],
            "数据改写": False,
            "内容处理": "保持原样",
            "来源": "归档执行",
            "分层依据来源": "策略校验",
            "归档时间": date.today().isoformat(),
        }
        record.update(self._persist_archive(record))
        return record

    def _archive_no(self, file_row: dict[str, Any], policy_version: str) -> str:
        file_no = str(file_row.get("文件编号") or "")
        # 同一文件同一策略版本只保留一份归档结果：重新归档时覆盖历史记录，
        # 避免聚合侧读到两份互相矛盾的层级
        for row in store.rows(ARCHIVE_MODULE):
            if (
                str(row.get("文件编号") or "") == file_no
                and str(row.get("策略版本") or "") == policy_version
            ):
                return str(row.get("归档编号") or "")
        return f"ARCH-{store.next_id(ARCHIVE_MODULE):04d}"

    def _persist_archive(self, record: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(ARCHIVE_MODULE)
        for row in rows:
            if str(row.get("归档编号") or "") == str(record["归档编号"]):
                row.update(record)
                row["pending"] = row["归档状态"] in (ARCHIVE_RETAINED, ARCHIVE_UNTIERED)
                row["abnormal"] = row["归档状态"] == ARCHIVE_UNTIERED
                return {"id": row.get("id")}
        record["id"] = store.next_id(ARCHIVE_MODULE)
        record["pending"] = record["归档状态"] in (ARCHIVE_RETAINED, ARCHIVE_UNTIERED)
        record["abnormal"] = record["归档状态"] == ARCHIVE_UNTIERED
        rows.append(record)
        return {"id": record["id"]}

    def _recovery_state(self, file_row: dict[str, Any]) -> str:
        recovery_no = str(file_row.get("恢复引用") or "").strip()
        if not recovery_no:
            return ""
        for row in store.rows(RECOVERY_MODULE):
            if str(row.get("断点编号") or "") == recovery_no:
                return str(row.get("状态") or "")
        for row in store.rows("resume"):
            if str(row.get("断点编号") or "") == recovery_no:
                return str(row.get("状态") or row.get("status") or "")
        return ""

    def _conflict_label(self, conflict: str) -> str:
        if conflict == "recovery":
            return "恢复终态优先"
        if conflict == "cleanup":
            return "清理终态优先"
        return "无冲突"

    def _summarize(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        tiers: dict[str, int] = {}
        for item in results:
            tier = str(item.get("归档层级") or "")
            tiers[tier] = tiers.get(tier, 0) + 1
        return {
            "total": len(results),
            "admitted": sum(1 for item in results if item.get("准入结果") == ADMITTED),
            "retained": sum(1 for item in results if item.get("归档状态") == ARCHIVE_RETAINED),
            "cleaned": sum(1 for item in results if item.get("归档状态") == ARCHIVE_CLEANED),
            "untiered": sum(1 for item in results if item.get("归档状态") == ARCHIVE_UNTIERED),
            "empty_files": sum(1 for item in results if item.get("归档层级") == EMPTY_FILE_TIER),
            "tiers": [{"归档层级": tier, "数量": count} for tier, count in sorted(tiers.items())],
            "items": results,
        }

    # ---- 清理清单与恢复清单 ---------------------------------------

    def _sync_lists(self, policy_version: str, precedence: str) -> None:
        """按归档结果同步清理清单与恢复清单，冲突结论与归档结果共用一份。"""
        archives = merge_storage.archive_rows(policy_version or None)
        cleanup_items: list[dict[str, Any]] = []
        recovery_items: list[dict[str, Any]] = []
        for archive in archives:
            status = str(archive.get("归档状态") or "")
            recovery_no = str(archive.get("恢复引用") or "")
            if recovery_no:
                new_entry = str(archive.get("新层级入口") or "")
                recovery_items.append({
                    "断点编号": recovery_no,
                    "文件编号": archive.get("文件编号"),
                    "文件名": archive.get("文件名"),
                    "归档编号": archive.get("归档编号"),
                    "策略版本": archive.get("策略版本"),
                    "原归档层级": archive.get("归档层级"),
                    "恢复终态": str(archive.get("恢复终态") or ""),
                    "冲突处理": str(archive.get("冲突处理") or "无冲突"),
                    "状态": RECOVERY_NEW_ENTRY if new_entry else RECOVERY_KEEP,
                    "新层级入口": new_entry,
                    "说明": (
                        f"清理终态优先，恢复入口改走新层级 {new_entry}"
                        if new_entry
                        else "恢复终态优先，继续引用原层级，文件未被删除或改写"
                    ),
                })
            if status not in (ARCHIVE_RETAINED, ARCHIVE_CLEANED):
                continue
            cleanup_items.append({
                "清理编号": self._cleanup_no(archive),
                "归档编号": archive.get("归档编号"),
                "文件编号": archive.get("文件编号"),
                "文件名": archive.get("文件名"),
                "策略版本": archive.get("策略版本"),
                "归档层级": archive.get("归档层级"),
                "目标层级": str(archive.get("目标层级") or archive.get("归档层级") or ""),
                "清理状态": CLEANUP_DEFERRED if status == ARCHIVE_RETAINED else CLEANUP_DONE,
                "清理依据": archive.get("清理依据"),
                "恢复引用": recovery_no,
                "冲突处理": str(archive.get("冲突处理") or "无冲突"),
                "新层级入口": str(archive.get("新层级入口") or ""),
                "延迟原因": (
                    str(archive.get("延迟原因") or "恢复终态优先，清理延后")
                    if status == ARCHIVE_RETAINED
                    else ""
                ),
            })
        # 同一断点+文件只保留一条恢复入口，归档执行的结论优先于历史记录
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in recovery_items:
            deduped[(str(item.get("断点编号")), str(item.get("文件编号")))] = item
        store.rows(CLEANUP_MODULE)[:] = cleanup_items
        store.rows(RECOVERY_MODULE)[:] = list(deduped.values())

    def _cleanup_no(self, archive: dict[str, Any]) -> str:
        archive_no = str(archive.get("归档编号") or "")
        return f"CLEAN-{archive_no.rsplit('-', 1)[-1]}"

    def rebuild_cleanup(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """按归档结果重建清理清单与恢复清单。

        冲突结论沿用归档时确定的终态优先级；``终态优先级`` 只对还没有结论的历史
        记录生效，避免重建清单时把已归档的“恢复保留”改成“已清理”。
        """
        precedence = str(values.get("终态优先级") or RECOVERY_PRECEDENCE).strip()
        policy_version = text_param(values.get("策略版本")) or ""
        with store.lock:
            if precedence not in PRECEDENCE_OPTIONS:
                return None, f"终态优先级「{precedence}」不在支持范围：{'、'.join(PRECEDENCE_OPTIONS)}"
            archives = merge_storage.archive_rows(policy_version or None)
            if not archives:
                return None, "没有分层归档结果，无法重建清理清单"
            reprioritized = 0
            for archive in archives:
                update = storage_policy.reprioritize_archive(archive, precedence)
                if update is None:
                    continue
                archive.update(update)
                reprioritized += 1
            self._sync_lists(policy_version, precedence)
            cleanup_rows = [dict(row) for row in store.rows(CLEANUP_MODULE)]
            recovery_rows = [dict(row) for row in store.rows(RECOVERY_MODULE)]
            merge_storage.refresh(policy_version or None)
        deferred = sum(1 for item in cleanup_rows if item["清理状态"] == CLEANUP_DEFERRED)
        return {
            "total": len(cleanup_rows),
            "deferred": deferred,
            "cleaned": sum(1 for item in cleanup_rows if item["清理状态"] == CLEANUP_DONE),
            "reprioritized": reprioritized,
            "items": cleanup_rows,
            "recovery_items": recovery_rows,
        }, f"清理清单已重建：{len(cleanup_rows)} 项，延迟 {deferred} 项，改判 {reprioritized} 项"

    # ---- 历史回填 -------------------------------------------------

    def backfill(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """历史记录缺少分层依据时按原层级回填；失败跳过并从已迁移记录继续。

        只处理已提交的归档记录；重复回填按“归档编号+文件编号”幂等，
        不会产生第二份迁移记录。
        """
        policy_version = text_param(values.get("策略版本")) or ""
        with store.lock:
            files = {str(row.get("文件编号") or ""): dict(row) for row in store.rows(FILE_MODULE)}
            migrated = {
                (str(row.get("归档编号") or ""), str(row.get("文件编号") or ""))
                for row in store.rows(MIGRATION_MODULE)
            }
            archives = [
                row for row in merge_storage.archive_rows(policy_version or None)
                if row.get("来源") == "历史" or not str(row.get("分层依据") or "").strip()
            ]
            archived = 0
            migrated_now = 0
            failed: list[dict[str, Any]] = []
            skipped = 0
            for archive in sorted(archives, key=lambda row: int(row.get("id", 0))):
                key = (str(archive.get("归档编号") or ""), str(archive.get("文件编号") or ""))
                if key in migrated:
                    skipped += 1
                    continue
                decision = storage_policy.decide_backfill(archive, files)
                if decision.get("skipped"):
                    skipped += 1
                    continue
                if not decision.get("ok"):
                    failed.append({"归档编号": key[0], "文件编号": key[1], "原因": decision.get("reason", "回填失败")})
                    continue
                archive.update(storage_policy.backfill_fields(decision))
                related_file = files.get(key[1])
                if related_file is not None:
                    store_file = next(
                        (row for row in store.rows(FILE_MODULE) if str(row.get("文件编号") or "") == key[1]),
                        None,
                    )
                    if store_file is not None:
                        store_file["最近扫描时间"] = date.today().isoformat()
                archived += 1
                migrated.add(key)
                next_id = store.next_id(MIGRATION_MODULE)
                store.rows(MIGRATION_MODULE).append({
                    "id": next_id,
                    "迁移编号": f"MIGR-{next_id:04d}",
                    "归档编号": key[0],
                    "文件编号": key[1],
                    "策略版本": archive.get("策略版本"),
                    "回填层级": decision["tier"],
                    "回填依据": decision["reason"],
                    "回填状态": "已迁移",
                    "回填时间": date.today().isoformat(),
                    "来源": "历史回填",
                })
                migrated_now += 1
            self._sync_lists(policy_version, RECOVERY_PRECEDENCE)
            merge_storage.refresh(policy_version or None)
        return {
            "archived": archived,
            "migrated": migrated_now,
            "skipped": skipped,
            "failed": failed,
        }, f"历史回填完成：回填 {archived} 条，新增迁移 {migrated_now} 条，失败 {len(failed)} 条"

    # ---- 扫描存储文件 ---------------------------------------------

    def scan_files(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """扫描存储文件并刷新归档页：空文件层级与恢复引用分别展示。"""
        policy_version = text_param(values.get("策略版本")) or merge_storage.latest_version()
        with store.lock:
            rules = storage_policy.build_tier_rules(store.rows(MODULE), policy_version)
            if not rules:
                return None, f"策略版本 {policy_version} 下没有分层规则，无法刷新归档页"
            today_text = date.today().isoformat()
            scanned: list[dict[str, Any]] = []
            for file_row in store.rows(FILE_MODULE):
                tiering = storage_policy.decide_tiering(file_row, rules)
                file_row["最近扫描时间"] = today_text
                empty = tiering["tier"] == EMPTY_FILE_TIER
                recovery_no = str(file_row.get("恢复引用") or "")
                scanned.append({
                    "文件编号": file_row.get("文件编号"),
                    "文件名": file_row.get("文件名"),
                    "文件格式": file_row.get("文件格式"),
                    "文件大小": file_row.get("文件大小"),
                    "创建时间": file_row.get("创建时间"),
                    "空文件": empty,
                    "空文件层级": EMPTY_FILE_TIER if empty else "",
                    "归档层级": tiering["tier"],
                    "目标层级": tiering["target_tier"],
                    "准入结果": ADMITTED if tiering["admitted"] else REJECTED,
                    "保留天数": tiering["retention_days"],
                    "到期日期": tiering["retention_due"] or "",
                    "分层依据": tiering["reason"],
                    "恢复引用": recovery_no,
                    "恢复引用显示": f"被恢复检查点 {recovery_no} 引用" if recovery_no else "无恢复引用",
                    "恢复终态": self._recovery_state(file_row),
                    "原存储层级": file_row.get("原存储层级"),
                })
            store.rows("storage_scan")[:] = [dict(item) for item in scanned]
            merge_storage.refresh(policy_version)
        empty_count = sum(1 for item in scanned if item["空文件"])
        recovery_count = sum(1 for item in scanned if item["恢复引用"])
        return {
            "policy_version": policy_version,
            "total": len(scanned),
            "empty_files": empty_count,
            "recovery_files": recovery_count,
            "items": scanned,
        }, f"扫描完成：{len(scanned)} 个文件，空文件 {empty_count} 个，恢复引用 {recovery_count} 个"

    # ---- 读侧 -----------------------------------------------------

    def list_archive(
        self,
        *,
        policy_version: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        policy_version = text_param(policy_version)
        status = text_param(status)
        with store.lock:
            rows = [self._archive_view(row) for row in store.rows(ARCHIVE_MODULE)]
        if policy_version:
            rows = [row for row in rows if str(row.get("策略版本") or "") == policy_version]
        if status:
            rows = [row for row in rows if row.get("归档状态") == status]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def _archive_view(self, row: dict[str, Any]) -> dict[str, Any]:
        """归档结果读侧补齐可选字段，历史记录与执行记录字段口径一致。"""
        view = dict(row)
        view.setdefault("归档层级", "")
        view.setdefault("目标层级", view.get("归档层级") or "")
        view.setdefault("准入结果", "拒绝")
        view.setdefault("归档状态", ARCHIVE_UNTIERED)
        view.setdefault("分层依据", "")
        view.setdefault("清理依据", "")
        view.setdefault("恢复引用", "")
        view.setdefault("恢复终态", "")
        view.setdefault("冲突处理", "无冲突")
        view.setdefault("清理状态", "")
        view.setdefault("延迟原因", "")
        view.setdefault("新层级入口", "")
        view.setdefault("数据改写", False)
        view.setdefault("内容处理", "保持原样")
        view.setdefault("策略版本", "")
        return view

    def list_cleanup(self, *, status: str | None = None) -> list[dict[str, Any]]:
        status = text_param(status)
        with store.lock:
            rows = [dict(row) for row in store.rows(CLEANUP_MODULE)]
        if status:
            rows = [row for row in rows if row.get("清理状态") == status]
        return rows

    def list_recovery(self, *, status: str | None = None) -> list[dict[str, Any]]:
        status = text_param(status)
        with store.lock:
            rows = [dict(row) for row in store.rows(RECOVERY_MODULE)]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return rows

    def list_files(self, *, policy_version: str | None = None) -> list[dict[str, Any]]:
        """归档页读取：有扫描结果时用扫描结果，否则按当前策略版本即时计算。"""
        with store.lock:
            scanned = [dict(row) for row in store.rows("storage_scan")]
            if scanned:
                return scanned
        version = text_param(policy_version) or merge_storage.latest_version()
        rules = storage_policy.build_tier_rules(store.rows(MODULE), version)
        items: list[dict[str, Any]] = []
        for file_row in store.rows(FILE_MODULE):
            tiering = storage_policy.decide_tiering(file_row, rules)
            empty = tiering["tier"] == EMPTY_FILE_TIER
            recovery_no = str(file_row.get("恢复引用") or "")
            items.append({
                "文件编号": file_row.get("文件编号"),
                "文件名": file_row.get("文件名"),
                "文件格式": file_row.get("文件格式"),
                "文件大小": file_row.get("文件大小"),
                "创建时间": file_row.get("创建时间"),
                "空文件": empty,
                "空文件层级": EMPTY_FILE_TIER if empty else "",
                "归档层级": tiering["tier"],
                "目标层级": tiering["target_tier"],
                "准入结果": ADMITTED if tiering["admitted"] else REJECTED,
                "保留天数": tiering["retention_days"],
                "到期日期": tiering["retention_due"] or "",
                "分层依据": tiering["reason"],
                "恢复引用": recovery_no,
                "恢复引用显示": f"被恢复检查点 {recovery_no} 引用" if recovery_no else "无恢复引用",
                "恢复终态": self._recovery_state(file_row),
                "原存储层级": file_row.get("原存储层级"),
            })
        return items

    def list_migrations(self) -> list[dict[str, Any]]:
        with store.lock:
            return [dict(row) for row in store.rows(MIGRATION_MODULE)]

    def archive_stats(self) -> dict[str, Any]:
        with store.lock:
            archives = [dict(row) for row in store.rows(ARCHIVE_MODULE)]
            cleanup = [dict(row) for row in store.rows(CLEANUP_MODULE)]
            recovery = [dict(row) for row in store.rows(RECOVERY_MODULE)]
            migrations = len(store.rows(MIGRATION_MODULE))
        return {
            "policy_versions": merge_storage.policy_versions(),
            "archive_total": len(archives),
            "usable_total": sum(1 for row in archives if row.get("归档状态") == ARCHIVE_USABLE),
            "retained_total": sum(1 for row in archives if row.get("归档状态") == ARCHIVE_RETAINED),
            "cleaned_total": sum(1 for row in archives if row.get("归档状态") == ARCHIVE_CLEANED),
            "untiered_total": sum(1 for row in archives if row.get("归档状态") == ARCHIVE_UNTIERED),
            "cleanup_total": len(cleanup),
            "cleanup_deferred": sum(1 for row in cleanup if row.get("清理状态") == CLEANUP_DEFERRED),
            "recovery_total": len(recovery),
            "recovery_new_entry": sum(1 for row in recovery if row.get("状态") == RECOVERY_NEW_ENTRY),
            "migration_total": migrations,
        }
