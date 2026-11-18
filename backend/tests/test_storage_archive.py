"""分层归档链路的规则回归测试。

覆盖：策略版本隔离、格式与保留期准入、空文件层级、恢复/清理冲突两种优先级、
历史回填的失败续跑与幂等、聚合侧只引用同一版本的可用层级。
运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.services import merge_storage, storage_policy
from app.services.storage import StorageService
from app.store import store


def reset_store() -> None:
    """每个用例从示例数据重新开始，避免互相影响。"""
    store.reset()


def days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


class TieringRuleTest(unittest.TestCase):
    def test_rejects_unsupported_format_and_empty_file(self) -> None:
        rules = storage_policy.build_tier_rules(store.rows("storage"), "v1")
        bad_format = storage_policy.decide_tiering(
            {"文件编号": "F1", "文件格式": "csv", "文件大小": 10, "创建时间": days_ago(1)}, rules
        )
        self.assertFalse(bad_format["admitted"])
        self.assertEqual(bad_format["tier"], storage_policy.ARCHIVE_UNTIERED)
        empty = storage_policy.decide_tiering(
            {"文件编号": "F2", "文件格式": "parquet", "文件大小": 0, "创建时间": days_ago(1)}, rules
        )
        self.assertEqual(empty["tier"], storage_policy.EMPTY_FILE_TIER)
        self.assertFalse(empty["admitted"])

    def test_admits_by_age_and_computes_retention_due(self) -> None:
        rules = storage_policy.build_tier_rules(store.rows("storage"), "v1")
        decision = storage_policy.decide_tiering(
            {"文件编号": "F3", "文件格式": "parquet", "文件大小": 10, "创建时间": days_ago(5)}, rules
        )
        self.assertTrue(decision["admitted"])
        self.assertEqual(decision["tier"], "热层")
        self.assertEqual(decision["retention_due"], (date.today() + timedelta(days=25)).isoformat())

    def test_expired_file_is_rejected_with_cleanup_basis(self) -> None:
        rules = storage_policy.build_tier_rules(store.rows("storage"), "v1")
        decision = storage_policy.decide_tiering(
            {"文件编号": "F4", "文件格式": "parquet", "文件大小": 10, "创建时间": days_ago(1200)}, rules
        )
        self.assertFalse(decision["admitted"])
        self.assertTrue(decision["expired"])
        self.assertIn("超过保留期", decision["cleanup_basis"])


class ArchiveWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = StorageService()
        self.service.scan_files({})

    def test_one_policy_version_per_archive(self) -> None:
        summary, _ = self.service.run_archive({"策略版本": "v2", "终态优先级": "恢复终态优先"})
        self.assertTrue(summary)
        self.assertEqual({item["策略版本"] for item in summary["items"]}, {"v2"})
        self.assertEqual(
            {row["策略版本"] for row in merge_storage.archive_rows("v2")}, {"v2"}
        )
        # v1 尚未归档，不能被 v2 的结果污染
        self.assertEqual(merge_storage.archive_rows("v1"), [])

    def test_recovery_precedence_keeps_data_and_defers_cleanup(self) -> None:
        self.service.run_archive({"策略版本": "v1", "终态优先级": "恢复终态优先"})
        archives, _ = self.service.list_archive(policy_version="v1", size=100)
        by_file = {row["文件编号"]: row for row in archives}
        conflict = by_file["FILE-0004"]
        self.assertEqual(conflict["归档状态"], "恢复保留")
        self.assertEqual(conflict["清理状态"], "已延迟")
        self.assertFalse(conflict["数据改写"])
        self.assertEqual(conflict["内容处理"], "保持原样")

        cleanup = self.service.list_cleanup()
        deferred = [row for row in cleanup if row["文件编号"] == "FILE-0004"]
        self.assertEqual(deferred[0]["清理状态"], "已延迟")
        self.assertTrue(deferred[0]["延迟原因"])
        self.assertTrue(all(row["状态"] == "保持原层级" for row in self.service.list_recovery()))

    def test_cleanup_precedence_marks_cleaned_and_adds_new_entry(self) -> None:
        self.service.run_archive({"策略版本": "v1", "终态优先级": "清理终态优先"})
        archives, _ = self.service.list_archive(policy_version="v1", size=100)
        conflict = {row["文件编号"]: row for row in archives}["FILE-0004"]
        self.assertEqual(conflict["归档状态"], "已清理")
        self.assertEqual(conflict["冲突处理"], "清理终态优先")
        self.assertTrue(conflict["新层级入口"])

        recovery = {row["断点编号"]: row for row in self.service.list_recovery()}
        self.assertEqual(recovery["RESU-0002"]["状态"], "新层级入口")
        self.assertTrue(recovery["RESU-0002"]["新层级入口"])

    def test_rebuild_cleanup_does_not_rewrite_final_conclusions(self) -> None:
        self.service.run_archive({"策略版本": "v1", "终态优先级": "清理终态优先"})
        # 重建时传恢复优先，也不能把已经“已清理”的结论改回“恢复保留”
        self.service.rebuild_cleanup({"策略版本": "v1", "终态优先级": "恢复终态优先"})
        archives, _ = self.service.list_archive(policy_version="v1", size=100)
        conflict = {row["文件编号"]: row for row in archives}["FILE-0004"]
        self.assertEqual(conflict["归档状态"], "已清理")

    def test_scan_reports_empty_and_recovery_separately(self) -> None:
        result, _ = self.service.scan_files({})
        empty = [row for row in result["items"] if row["空文件"]]
        recovery = [row for row in result["items"] if row["恢复引用"]]
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0]["空文件层级"], "空文件")
        self.assertTrue(all("引用" in row["恢复引用显示"] for row in recovery))


class BackfillTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = StorageService()

    def test_backfill_continues_after_failure_and_is_idempotent(self) -> None:
        first, _ = self.service.backfill({})
        self.assertEqual(first["archived"], 2)
        self.assertEqual(len(first["failed"]), 2)
        migrations = self.service.list_migrations()
        self.assertEqual(
            sorted((row["归档编号"], row["文件编号"]) for row in migrations),
            [("ARCH-9001", "FILE-0010"), ("ARCH-9004", "FILE-0001")],
        )

        second, _ = self.service.backfill({})
        self.assertEqual(second["migrated"], 0)
        self.assertEqual(len(self.service.list_migrations()), 2)

    def test_backfill_uses_original_tier(self) -> None:
        self.service.backfill({})
        archives, _ = self.service.list_archive(policy_version="v0", size=100)
        by_no = {row["归档编号"]: row for row in archives}
        self.assertEqual(by_no["ARCH-9001"]["归档层级"], "温层")
        self.assertEqual(by_no["ARCH-9001"]["分层依据来源"], "按原层级回填")


class MergeReferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = StorageService()
        self.service.scan_files({})
        self.service.run_archive({"策略版本": "v1", "终态优先级": "恢复终态优先"})
        self.service.run_archive({"策略版本": "v2", "终态优先级": "清理终态优先"})

    def test_reference_only_uses_usable_tiers_of_same_version(self) -> None:
        reference = merge_storage.reference("v2")
        self.assertEqual(reference["policy_version"], "v2")
        for item in reference["items"]:
            self.assertEqual(item["策略版本"], "v2")
            self.assertIn(item["归档层级"], reference["usable_tiers"])
            self.assertEqual(item["归档状态"], "可用")
        # v2 的冷层是暂停状态，不应出现在可用层级里
        self.assertNotIn("冷层", reference["usable_tiers"])

    def test_admission_queue_is_scoped_by_version(self) -> None:
        v1, _ = merge_storage.list_admission(policy_version="v1", size=100)
        v2, _ = merge_storage.list_admission(policy_version="v2", size=100)
        self.assertTrue(v1 and v2)
        self.assertTrue(all(row["策略版本"] == "v1" for row in v1))
        self.assertTrue(all(row["策略版本"] == "v2" for row in v2))

    def test_sync_refreshes_both_lists(self) -> None:
        merge_storage.refresh("v1")
        summary = merge_storage.refresh("v1")
        self.assertGreaterEqual(summary["queue_total"], 1)
        # 指定版本刷新只重算该版本，另一版本的已提交准入项原样保留
        versions = {row["策略版本"] for row in store.rows("merge_storage_admission")}
        self.assertEqual(versions, {"v1", "v2"})
        v1_rows = [row for row in store.rows("merge_storage_admission") if row["策略版本"] == "v1"]
        self.assertEqual(len(v1_rows), len(merge_storage.archive_rows("v1")))
        # 全量刷新时两个版本一起重算
        full = merge_storage.refresh()
        self.assertEqual(
            len(store.rows("merge_storage_admission")),
            len(store.rows("storage_archive")),
        )
        self.assertGreaterEqual(full["queue_total"], summary["queue_total"])


if __name__ == "__main__":
    unittest.main()
