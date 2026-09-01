"""自启动管理 — 将 MCP Server 注册为系统服务，开机自启 + 崩溃自动拉起。

支持平台：
- macOS: launchd LaunchAgent（RunAtLoad + KeepAlive）
- 其他平台：暂不支持（明确报错，不做未验证的实现）

命令: zigtester install|uninstall|status
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

_LABEL = "com.fixnet.zigtester"
_PLIST_NAME = f"{_LABEL}.plist"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _uid() -> int:
    return os.getuid()


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    return _launch_agents_dir() / _PLIST_NAME


def _log_path() -> Path:
    d = Path.home() / ".zigtester"
    d.mkdir(parents=True, exist_ok=True)
    return d / "launchd.log"


def _default_dir() -> str:
    """默认工作目录 — 与 CLI 的 ZIGTESTER_ROOT 约定一致。"""
    return os.environ.get("ZIGTESTER_ROOT", os.getcwd())


def _is_loaded() -> bool:
    """服务是否已加载到 launchd。"""
    rc = subprocess.run(
        ["launchctl", "print", f"gui/{_uid()}/{_LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode
    return rc == 0


def _bootout() -> bool:
    """卸载 launchd 服务（若未加载则返回 False）。"""
    rc = subprocess.run(
        ["launchctl", "bootout", f"gui/{_uid()}/{_LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode
    return rc == 0


def _wait_unloaded(timeout: float = 5.0) -> bool:
    """轮询等待 label 释放。

    launchctl bootout 是异步的：返回 0 后 label 仍需约 1 秒才真正释放，
    若立即 bootstrap 会报 "Bootstrap failed: 5: Input/output error"。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_loaded():
            return True
        time.sleep(0.2)
    return False


def _plist_content(working_dir: str) -> bytes:
    """生成 launchd plist（XML 格式，便于 plutil 校验）。"""
    plist = {
        "Label": _LABEL,
        "ProgramArguments": [sys.executable, "-m", "zigtester.server"],
        "WorkingDirectory": working_dir,
        "RunAtLoad": True,
        "KeepAlive": True,
        # Interactive（勿改回 Background）：MCP 是交互式测试入口，测试常派生 ~1GB
        # 编译（zigoutbounds unit 主测试二进制，本地直跑 MaxRSS:1G / 20s 正常完成）。
        # Background 类型对该进程及子进程施加 jetsam 内存配额 + 降调度，实测把该
        # 编译杀在 ~900M（1m 后 MaxRSS:806M~923M，pool_tests 35M 却正常）——非代码
        # bug，属 launchd 进程类型配额。见 zigbox task_plan.md zt-9。
        "ProcessType": "Interactive",
        "StandardOutPath": str(_log_path()),
        "StandardErrorPath": str(_log_path()),
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML)


def cmd_install(args: argparse.Namespace) -> int:
    """install 子命令 — 安装自启动。"""
    if not _is_macos():
        print(f"自启动当前仅支持 macOS（当前平台: {sys.platform}）", file=sys.stderr)
        return 1

    working_dir = os.path.abspath(args.dir or _default_dir())

    plist_path = _plist_path()
    _launch_agents_dir().mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(_plist_content(working_dir))

    # 已加载则先卸载，避免 bootstrap 重复加载报错
    if _is_loaded():
        _bootout()
        if not _wait_unloaded():
            print("卸载旧服务超时，仍尝试重新加载", file=sys.stderr)

    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist_path)],
        check=True,
    )
    subprocess.run(
        ["launchctl", "enable", f"gui/{_uid()}/{_LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{_uid()}/{_LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print(f"自启动已安装: {plist_path}")
    print(f"  工作目录: {working_dir}")
    print(f"  Python:   {sys.executable}")
    print(f"  日志:     {_log_path()}")
    print("  MCP URL:  http://127.0.0.1:9020/mcp")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """uninstall 子命令 — 卸载自启动。"""
    if not _is_macos():
        print(f"自启动当前仅支持 macOS（当前平台: {sys.platform}）", file=sys.stderr)
        return 1

    _bootout()

    plist_path = _plist_path()
    if plist_path.exists():
        plist_path.unlink()
        print(f"自启动已卸载: {plist_path}")
    else:
        print("自启动未安装")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """status 子命令 — 查看自启动状态。"""
    if not _is_macos():
        print(f"自启动当前仅支持 macOS（当前平台: {sys.platform}）", file=sys.stderr)
        return 1

    loaded = _is_loaded()
    plist_exists = _plist_path().exists()

    print(f"plist 文件: {'存在' if plist_exists else '缺失'} ({_plist_path()})")
    print(f"launchd 服务: {'已加载' if loaded else '未加载'} ({_LABEL})")

    if loaded:
        try:
            out = subprocess.run(
                ["launchctl", "print", f"gui/{_uid()}/{_LABEL}"],
                capture_output=True, text=True,
            ).stdout
            pid_line = next(
                (ln.strip() for ln in out.splitlines() if "pid" in ln.lower()),
                None,
            )
            if pid_line:
                print(f"  {pid_line}")
        except Exception:
            pass

    return 0 if (loaded and plist_exists) else 1
