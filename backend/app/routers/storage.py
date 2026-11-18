"""采集存储分层接口：策略维护 + 分层归档、清理清单、历史回填与文件扫描。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import merge_storage
from app.services.storage import StorageService

router = APIRouter(prefix="/api/storage", tags=["采集存储分层"])

service = StorageService()

LIST_FIELDS = ["策略编号", "存储层级", "文件格式", "压缩算法", "保留天数", "迁移规则", "清理方式", "策略状态"]
STATUSES = ["草稿", "已生效", "已暂停", "已关闭"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按策略编号检索"),
    status: str | None = Query(default=None, description="草稿、已生效、已暂停、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按策略编号与状态过滤采集存储分层列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")

def export_entries() -> dict[str, Any]:
    """导出采集存储分层清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "storage", "total": total, "items": items}


@router.get("/archive", response_model=dict)
def list_archive(
    policy_version: str | None = Query(default=None, description="策略版本，缺省返回全部版本"),
    status: str | None = Query(default=None, description="可用、恢复保留、已清理、未分层"),
    page: int = 1,
    size: int = 200,
) -> dict[str, Any]:
    """分层归档结果：按策略版本与归档状态核对层级和清理依据。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = service.list_archive(policy_version=policy_version, status=status, page=page, size=size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "policy_versions": merge_storage.policy_versions(),
        "stats": service.archive_stats(),
    }


@router.get("/cleanup", response_model=dict)
def list_cleanup(status: str | None = Query(default=None, description="待清理、已延迟、已清理")) -> dict[str, Any]:
    """清理清单：延迟项与已清理项都带清理依据和冲突说明。"""
    items = service.list_cleanup(status=status)
    return {
        "items": items,
        "total": len(items),
        "deferred": sum(1 for item in items if item.get("清理状态") == "已延迟"),
    }


@router.get("/recovery", response_model=dict)
def list_recovery(status: str | None = Query(default=None, description="新层级入口、保持原层级")) -> dict[str, Any]:
    """恢复清单：清理终态优先后出现新层级入口，恢复终态优先时保持原层级。"""
    items = service.list_recovery(status=status)
    return {
        "items": items,
        "total": len(items),
        "new_entry": sum(1 for item in items if item.get("状态") == "新层级入口"),
    }


@router.get("/files", response_model=dict)
def list_files(policy_version: str | None = Query(default=None, description="策略版本，缺省取最新版本")) -> dict[str, Any]:
    """归档页文件列表：空文件层级与恢复引用分别显示。"""
    items = service.list_files(policy_version=policy_version)
    return {
        "items": items,
        "total": len(items),
        "empty_files": sum(1 for item in items if item.get("空文件")),
        "recovery_files": sum(1 for item in items if item.get("恢复引用")),
        "policy_version": policy_version or merge_storage.latest_version(),
    }


@router.get("/migrations", response_model=dict)
def list_migrations() -> dict[str, Any]:
    """回填产生的迁移记录：按归档编号+文件编号可核对唯一性。"""
    items = service.list_migrations()
    return {"items": items, "total": len(items)}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条存储策略明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"存储策略 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条存储策略，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="存储策略已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条存储策略执行启用策略、暂停策略、关闭策略；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/archive", response_model=ActionResult)
def run_archive(payload: EntryPayload) -> ActionResult:
    """执行分层归档：一次只使用一个策略版本，结果进入聚合模块的新增准入队列。"""
    summary, message = service.run_archive(payload.values)
    if summary is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=summary)


@router.post("/cleanup/rebuild", response_model=ActionResult)
def rebuild_cleanup(payload: EntryPayload) -> ActionResult:
    """重建清理清单与恢复清单；冲突按终态优先级落到延迟项或新层级入口。"""
    summary, message = service.rebuild_cleanup(payload.values)
    if summary is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=summary)


@router.post("/backfill", response_model=ActionResult)
def backfill(payload: EntryPayload) -> ActionResult:
    """历史记录缺少分层依据时按原层级回填；失败项跳过，已迁移记录不重复迁移。"""
    summary, message = service.backfill(payload.values)
    if summary is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=summary)


@router.post("/scan", response_model=ActionResult)
def scan_files(payload: EntryPayload) -> ActionResult:
    """扫描存储文件并刷新归档页；空文件层级与恢复引用分别返回。"""
    summary, message = service.scan_files(payload.values)
    if summary is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=summary)


@router.post("/merge-sync", response_model=ActionResult)
def sync_merge(payload: EntryPayload) -> ActionResult:
    """另一个入口：同步刷新聚合模块的新增准入清单与分层归档结果快照。"""
    policy_version = str(payload.values.get("策略版本") or "").strip() or None
    summary = merge_storage.refresh(policy_version)
    return ActionResult(
        ok=True,
        message=(
            f"聚合模块已刷新：策略版本 {summary['policy_version']}，"
            f"准入队列 {summary['queue_total']} 条"
        ),
        entry=summary,
    )
