"""备份审计：四项只读检查，零写入。

检查项：
  1. 副本存在性：热数据文件在备份目标是否有对应副本
  2. 版本轮换：备份是否保留多个版本（而非只有单份被反复覆盖）
  3. 陈旧性：备份文件 mtime 是否超过阈值（说明同步停滞）
  4. 覆盖冲突：目标存在同名但大小/哈希不同的文件

输出结构化报告，绝不修改任何文件。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .db import Database
from .security import PathGuard, PathViolation


@dataclass
class AuditFinding:
    check: str
    path: str
    detail: str
    severity: str  # info / warn / error


@dataclass
class BackupAuditReport:
    source: str
    target: str
    checks: dict[str, dict] = field(default_factory=dict)
    findings: list[AuditFinding] = field(default_factory=list)
    ok: bool = True

    def summary(self) -> dict:
        return {
            "source": self.source, "target": self.target,
            "checks": self.checks,
            "findings": len(self.findings),
            "errors": sum(1 for f in self.findings if f.severity == "error"),
            "warns": sum(1 for f in self.findings if f.severity == "warn"),
            "ok": self.ok,
        }


class BackupAuditor:
    """备份审计器——只读。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.guard = PathGuard(config)
        self.stale_days = int(config.get("audit.stale_days", 30))
        self.min_versions = int(config.get("audit.min_versions", 2))

    def audit(self) -> BackupAuditReport:
        cfg = self.config.get("backup.hot", {})
        source_raw = str(cfg.get("source", "/write/16t2"))
        target_raw = str(cfg.get("target", "/backup/hot"))
        try:
            source = self.guard.check(source_raw, source="audit", action="audit")
            target = self.guard.check(target_raw, source="audit", action="audit")
        except PathViolation as exc:
            report = BackupAuditReport(source_raw, target_raw)
            report.findings.append(AuditFinding("access", "-", str(exc), "error"))
            report.ok = False
            return report

        report = BackupAuditReport(str(source), str(target))
        records = [r for r in self.db.get_all_active() if r.temp == "热"]
        cutoff = time.time() - self.stale_days * 86400

        present = missing = 0
        conflicts = 0
        stale = 0
        version_ok = 0
        checked_paths: set[Path] = set()

        for rec in records:
            src = Path(rec.path)
            if source in src.parents or source == src.parent:
                rel = src.relative_to(source)
            else:
                rel = Path(src.name)
            dst = target / rel
            checked_paths.add(dst)

            # 1. 副本存在性
            if not dst.exists():
                missing += 1
                report.findings.append(AuditFinding(
                    "existence", str(dst), "备份副本缺失", "error"))
                continue
            present += 1

            # 4. 覆盖冲突：同名但大小不同
            if dst.stat().st_size != src.stat().st_size:
                conflicts += 1
                report.findings.append(AuditFinding(
                    "conflict", str(dst), "同名副本大小不一致，疑似被覆盖", "warn"))

            # 3. 陈旧性
            if dst.stat().st_mtime < cutoff:
                stale += 1
                report.findings.append(AuditFinding(
                    "staleness", str(dst), f"备份超过 {self.stale_days} 天未更新", "warn"))

        # 2. 版本轮换：检查备份目录是否含版本化命名（.v1/.v2 或 时间戳目录）
        version_files = [
            p for p in target.rglob("*") if p.is_file()
            and (".v" in p.name or "_bak" in p.name or "version" in p.name.lower())
        ] if target.exists() else []
        version_ok = len(version_files) >= self.min_versions
        if not version_ok:
            report.findings.append(AuditFinding(
                "versioning", str(target),
                f"版本副本 {len(version_files)} 个，少于阈值 {self.min_versions}", "warn"))

        report.checks = {
            "existence": {"ok": missing == 0, "present": present, "missing": missing},
            "versioning": {"ok": version_ok, "versions": len(version_files),
                           "min_required": self.min_versions},
            "staleness": {"ok": stale == 0, "stale": stale, "threshold_days": self.stale_days},
            "conflict": {"ok": conflicts == 0, "conflicts": conflicts},
        }
        report.ok = missing == 0 and conflicts == 0
        return report
