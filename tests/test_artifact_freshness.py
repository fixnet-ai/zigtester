#!/usr/bin/env python3
"""zt-12 产物新鲜度守卫单元测试。

实案（2026-11-19 zigtun）：zig-out 测试二进制三周陈旧，期间全部 sudo 套件
PASS 都是旧码跑的——zt-10 只拦「构建失败」，不拦「构建成功但 install 产物
陈旧」（build_command 未覆盖 install 步 / 未配置）。zt-12：声明 artifact 的
套件跑前校验产物 mtime ≥ 构建输入（本仓 src + path 依赖仓 src）最新 mtime，
陈旧/缺失即 ERROR。

判据锚取舍（同日现场推翻的第一版）：不用 git HEAD 提交时间——「先构建后提交」
工作流恒误报，且 zig install 缓存命中不刷新产物 mtime；改用源文件 mtime
（输入新于产物 = 确定没重编）。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zigtester.runner import _build_inputs_newest_mtime, _check_artifact_fresh  # noqa: E402


def _make_repo(tmp_path, with_dep: bool = False):
    """临时 zig 仓形态：src/main.zig + build.zig + zig-out/test-bin。

    产物 mtime 拨回 1h 前（比一切源码旧）。with_dep=True 再建 ../dep 仓并以
    build.zig.zon .path 挂上。
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "zig-out").mkdir()
    (repo / "src" / "main.zig").write_text("pub fn main() void {}\n")
    (repo / "build.zig").write_text("const std = @import(\"std\");\n")
    art = repo / "zig-out" / "test-bin"
    art.write_bytes(b"\x7fELF-fake")

    if with_dep:
        dep = tmp_path / "dep"
        (dep / "src").mkdir(parents=True)
        (dep / "src" / "lib.zig").write_text("pub const x = 1;\n")
        (dep / "build.zig").write_text("const std = @import(\"std\");\n")
        (repo / "build.zig.zon").write_text(
            ".{\n"
            "    .dependencies = .{\n"
            "        .dep = .{ .path = \"../dep\" },\n"
            "    },\n"
            "}\n"
        )

    # 产物拨旧 1h（比源码旧）
    old = time.time() - 3600
    os.utime(art, (old, old))
    return repo, art


def test_missing_artifact(tmp_path):
    repo, _ = _make_repo(tmp_path)
    msg = _check_artifact_fresh("zig-out/no-such-bin", str(repo))
    assert msg is not None and "不存在" in msg


def test_stale_artifact_source_newer(tmp_path):
    """源码比产物新（构建后改了源码没重编）→ 陈旧。"""
    repo, art = _make_repo(tmp_path)
    # 源码是「现在」写的，产物拨旧 1h → 输入新于产物
    msg = _check_artifact_fresh("zig-out/test-bin", str(repo))
    assert msg is not None and "陈旧" in msg and "假绿" in msg


def test_fresh_artifact_passes(tmp_path):
    """源码旧于产物（构建在改动之后）→ 过；「先构建后提交」不误报。"""
    repo, art = _make_repo(tmp_path)
    # 源码也拨旧 2h（早于构建）
    older = time.time() - 7200
    src = repo / "src" / "main.zig"
    bz = repo / "build.zig"
    os.utime(src, (older, older))
    os.utime(bz, (older, older))
    assert _check_artifact_fresh("zig-out/test-bin", str(repo)) is None


def test_path_dep_change_stales_artifact(tmp_path):
    """path 依赖仓源码新于产物 → 同样陈旧（zigtun×zigstack 实案：依赖改了主仓没重编）。"""
    repo, art = _make_repo(tmp_path, with_dep=True)
    # 主仓源码拨旧（早于产物构建——产物虽旧 1h 但主仓源码更旧）
    older = time.time() - 7200
    os.utime(repo / "src" / "main.zig", (older, older))
    os.utime(repo / "build.zig", (older, older))
    # 依赖仓源码是「现在」写的 → 比产物（旧 1h）新 → 陈旧
    msg = _check_artifact_fresh("zig-out/test-bin", str(repo))
    assert msg is not None and "陈旧" in msg


def test_glob_picks_newest_match(tmp_path):
    """glob 多匹配（交叉编译产物并存）取最新：本机新件在场即过，旧交叉件不误伤。"""
    repo, art = _make_repo(tmp_path)
    older = time.time() - 7200
    os.utime(repo / "src" / "main.zig", (older, older))
    os.utime(repo / "build.zig", (older, older))
    cross = repo / "zig-out" / "linux-aarch64"
    cross.mkdir()
    old_bin = cross / "test-bin"
    old_bin.write_bytes(b"old-cross")
    ancient = time.time() - 30 * 86400
    os.utime(old_bin, (ancient, ancient))
    assert _check_artifact_fresh("zig-out/**/test-bin", str(repo)) is None


def test_non_zig_dir_existence_only(tmp_path):
    """非 zig 仓形态（无 src/ 无 build.zig）：只查存在。"""
    d = tmp_path / "plain"
    (d / "zig-out").mkdir(parents=True)
    (d / "zig-out" / "bin").write_bytes(b"x")
    assert _check_artifact_fresh("zig-out/bin", str(d)) is None
    assert _check_artifact_fresh("zig-out/none", str(d)) is not None


def test_build_inputs_helper(tmp_path):
    repo, _ = _make_repo(tmp_path, with_dep=True)
    assert _build_inputs_newest_mtime(str(repo)) is not None
    assert _build_inputs_newest_mtime(str(tmp_path / "nowhere")) is None


def test_executor_stale_artifact_errors(tmp_path):
    """execute() 集成：artifact 陈旧 → 套件 ERROR（setup_error 带 zt-12），命令不执行。"""
    from zigtester.config import SuiteConfig
    from zigtester.runner import TestExecutor

    repo, _ = _make_repo(tmp_path)
    suite = SuiteConfig(
        name="stale-suite",
        command="python3 -c \"print('should-not-run')\"",
        timeout=30,
        artifact="zig-out/test-bin",
    )
    res = TestExecutor().execute(suite, str(repo))
    assert res.status == "ERROR"
    assert "zt-12" in (res.setup_error or "")
