"""热数据备份与冷数据校验."""

import shutil
import time
from pathlib import Path

from .config import Config
from .db import Database


class BackupManager:
    """基于 rsync 的备份与索引校验."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def backup_hot(self) -> dict:
        """把热数据同步到备份目标."""
        cfg = self.config.get("backup.hot", {})
        if not cfg.get("enabled", False):
            return {"skipped": True, "reason": "disabled"}
        source = Path(cfg.get("source", "/write/16t2"))
        target = Path(cfg.get("target", "/backup/hot"))
        max_age = int(cfg.get("max_age_days", 90))
        if not source.exists():
            return {"skipped": True, "reason": "source missing"}

        target.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - max_age * 86400
        records = [
            r for r in self.db.get_all_active()
            if r.temp == "热" and r.mtime >= cutoff and Path(r.path).exists()
        ]
        copied = 0
        for rec in records:
            src = Path(rec.path)
            rel = src.relative_to(source) if source in src.parents else Path(src.name)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dst)
                copied += 1
        return {"total": len(records), "copied": copied, "target": str(target)}

    def verify_cold(self) -> dict:
        """校验冷数据文件是否仍存在且大小未变."""
        records = [r for r in self.db.get_all_active() if r.temp == "冷"]
        missing = 0
        changed = 0
        for rec in records:
            p = Path(rec.path)
            if not p.exists():
                missing += 1
            elif p.stat().st_size != rec.size:
                changed += 1
        return {"total": len(records), "missing": missing, "changed": changed}
