"""D4 感知哈希相似图片检测：纯 Python DCT，无 numpy/opencv 重依赖。

设计约束（对应进度计划 #22）：
- 复用现有「大小分组」思路，只在同尺寸图片组内比较，降低误报。
- 感知哈希（pHash）对缩放/压缩后的同一张图仍近似，补足 SHA-256 只能查完全相同的短板。
- 只读：返回相似分组清单，绝不移动/删除文件；落地走既定计划+确认链路。
- Pillow 仅用于解码像素（可选依赖）；无 Pillow 时 capability().phash=False 并优雅降级。
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database
from .models import FileRecord

try:
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover
    Image = None

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "heic"}


def _dct1d(values: list[float]) -> list[float]:
    """一维 DCT-II（纯 Python），返回前 N 个系数。"""
    n = len(values)
    return [
        sum(values[x] * math.cos((2 * x + 1) * u * math.pi / (2 * n)) for x in range(n))
        for u in range(n)
    ]


def _dct2d(pixels: list[list[float]]) -> list[list[float]]:
    """可分离二维 DCT：先行后列。"""
    rows = [_dct1d(row) for row in pixels]
    n = len(rows)
    cols = [_dct1d([rows[r][c] for r in range(n)]) for c in range(n)]
    return [[cols[c][r] for c in range(n)] for r in range(n)]


def phash(path: str | Path, hash_size: int = 8, highfreq_factor: int = 4) -> int | None:
    """计算 64-bit 感知哈希；图片无法解码时返回 None。"""
    if Image is None:
        return None
    size = hash_size * highfreq_factor
    try:
        with Image.open(path) as img:
            img = img.convert("L").resize((size, size), Image.LANCZOS)
            pixels = [[float(v) for v in img.crop((0, y, size, y + 1)).getdata()]
                      for y in range(size)]
    except Exception:
        return None
    dct = _dct2d(pixels)
    low = [row[:hash_size] for row in dct[:hash_size]]
    flat = [v for row in low for v in row]
    median = sorted(flat)[len(flat) // 2]
    bits = 0
    for v in flat:
        bits = (bits << 1) | (1 if v > median else 0)
    return bits


def hamming(a: int, b: int) -> int:
    """两个哈希的汉明距离（差异位数）。"""
    return (a ^ b).bit_count()


class PerceptualFinder:
    """在图片库内查找感知相似的分组（只读）。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.threshold = int(config.get("perceptual.threshold", 10))
        self.hash_size = int(config.get("perceptual.hash_size", 8))
        self.enabled = bool(config.get("perceptual.enabled", True))

    @staticmethod
    def capability() -> dict[str, bool]:
        return {"phash": Image is not None}

    def find_similar(self, records: list[FileRecord] | None = None) -> dict[str, Any]:
        """同尺寸图片组内用 pHash 聚类，返回相似分组清单（只读）。"""
        if not self.enabled:
            return {"enabled": False, "groups": [], "similar_pairs": 0}
        if Image is None:
            return {"enabled": True, "available": False, "groups": [], "similar_pairs": 0,
                    "note": "缺少 Pillow，pHash 已跳过（不影响主流程）"}

        records = records if records is not None else self.db.get_all_active()
        images = [
            r for r in records
            if r.id and (r.ext or "").lower().lstrip(".") in IMAGE_EXTS
            and r.size > 0 and Path(r.path).exists()
        ]
        by_size: dict[int, list[FileRecord]] = defaultdict(list)
        for r in images:
            by_size[r.size].append(r)
        candidates = [g for g in by_size.values() if len(g) > 1]

        groups: list[dict[str, Any]] = []
        hashed = 0
        for group in candidates:
            hashes: list[tuple[FileRecord, int]] = []
            for rec in group:
                h = phash(rec.path, self.hash_size)
                hashed += 1
                if h is not None:
                    hashes.append((rec, h))
            # 并查集式朴素聚类：同尺寸组内两两比较
            clusters: list[list[tuple[FileRecord, int]]] = []
            for item in hashes:
                placed = False
                for cluster in clusters:
                    if any(hamming(item[1], other[1]) <= self.threshold for other in cluster):
                        cluster.append(item)
                        placed = True
                        break
                if not placed:
                    clusters.append([item])
            for cluster in clusters:
                if len(cluster) > 1:
                    groups.append({
                        "paths": [r.path for r, _ in cluster],
                        "file_ids": [r.id for r, _ in cluster],
                        "max_distance": max(
                            hamming(a[1], b[1])
                            for i, a in enumerate(cluster) for b in cluster[i + 1:]
                        ),
                        "wasted_bytes": sum(r.size for r, _ in cluster[1:]),
                    })
        groups.sort(key=lambda g: g["wasted_bytes"], reverse=True)
        return {
            "enabled": True, "available": True,
            "images_scanned": len(images), "hashed": hashed,
            "groups": groups, "similar_groups": len(groups),
            "similar_pairs": sum(len(g["paths"]) - 1 for g in groups),
        }
