"""配置发现 — 扫描目录树查找 zigtester.yaml。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ProjectConfig, parse_config


@dataclass
class DiscoveredProject:
    """扫描发现的项目。"""
    name: str
    path: str          # 项目根目录（配置所在目录）
    config_path: str   # zigtester.yaml 完整路径
    config: ProjectConfig
    levels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.levels = [
            name for name, lc in self.config.levels.items()
            if lc.suites
        ]


def find_config(start_dir: str) -> str | None:
    """从 start_dir 向上查找 zigtester.yaml，返回完整路径或 None。"""
    current = Path(start_dir).resolve()
    while True:
        candidate = current / "zigtester.yaml"
        if candidate.is_file():
            return str(candidate)
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _scan_dir(root: Path, max_depth: int = 3) -> list[Path]:
    """在 root 下递归查找 zigtester.yaml，最大深度 max_depth。"""
    found: list[Path] = []
    # 跳过隐藏目录和常用非项目目录
    skip = {".git", ".claude", ".codegraph", "node_modules", "__pycache__",
            ".venv", "venv", "vendor", "zig-cache", "zig-out"}

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            return

        # 当前目录优先检查
        yaml_path = current / "zigtester.yaml"
        if yaml_path.is_file():
            found.append(yaml_path)
            return  # 找到后不深入子目录

        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in skip or entry.name.startswith("."):
                continue
            _walk(entry, depth + 1)

    _walk(root, 0)
    return found


def discover(root_dir: str, recursive: bool = True) -> list[DiscoveredProject]:
    """扫描 root_dir，发现所有包含 zigtester.yaml 的项目。

    Args:
        root_dir: 扫描根目录
        recursive: 是否递归扫描子目录

    Returns:
        DiscoveredProject 列表，按项目名排序
    """
    root = Path(root_dir).resolve()
    projects: list[DiscoveredProject] = []

    if not recursive:
        # 仅检查根目录本身
        yaml_path = root / "zigtester.yaml"
        if yaml_path.is_file():
            try:
                cfg = parse_config(str(yaml_path))
                projects.append(DiscoveredProject(
                    name=cfg.project,
                    path=str(root),
                    config_path=str(yaml_path),
                    config=cfg,
                ))
            except Exception:
                pass  # 跳过无效配置
        return projects

    # 递归扫描
    yaml_files = _scan_dir(root)
    for yp in yaml_files:
        try:
            cfg = parse_config(str(yp))
            projects.append(DiscoveredProject(
                name=cfg.project,
                path=str(yp.parent),
                config_path=str(yp),
                config=cfg,
            ))
        except Exception:
            pass  # 跳过无效配置

    projects.sort(key=lambda p: p.name)
    return projects
