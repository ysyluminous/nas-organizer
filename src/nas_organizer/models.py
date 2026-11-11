"""数据类定义."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileRecord:
    """文件索引记录."""

    path: str
    volume: str
    filename: str
    ext: str
    size: int
    mtime: float
    atime: float
    ctime: float
    id: Optional[int] = None
    head_hash: Optional[str] = None
    tail_hash: Optional[str] = None
    full_hash: Optional[str] = None
    ftype: Optional[str] = None
    domain: Optional[str] = None
    temp: Optional[str] = None
    confidence: float = 1.0
    classified_by: Optional[str] = None
    is_duplicate: bool = False
    duplicate_of: Optional[int] = None
    status: str = "active"


@dataclass
class PlanItem:
    """计划明细."""

    file_id: int
    action: str
    src_path: str
    dst_path: Optional[str] = None
    reason: str = ""
    status: str = "pending"
    error: Optional[str] = None
    id: Optional[int] = None
    plan_id: Optional[int] = None


@dataclass
class Plan:
    """迁移计划单."""

    name: str
    description: str = ""
    id: Optional[int] = None
    status: str = "draft"
    item_count: int = 0
    items: list[PlanItem] = field(default_factory=list)


@dataclass
class OpLog:
    """操作日志（用于回滚）."""

    plan_item_id: int
    action: str
    src_path: str
    dst_path: str
    sha256: Optional[str] = None
    size: Optional[int] = None
    executed_at: float = 0.0
    rolled_back: bool = False
    id: Optional[int] = None
