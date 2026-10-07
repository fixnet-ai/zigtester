"""_netstat_line_owner_pid / _port_holder_diag 单测。

背景（2026-10-07 环境 Death lock 排查）：lsof 对 root 持有者不可见，
macOS `netstat -anv -p tcp` 仍给出 `name:pid` —— 本机常驻 KISS 特权服务
占住 kiss 产品 HTTP 门面口 19090 时，自愈清理失败但旧报错只猜「可能是
插件残留」。解析函数让 setup_error 能直接报出持有者。
"""

from zigtester.plugin import _netstat_line_owner_pid

# 本机 netstat -anv -p tcp 实录行（LISTEN + root 持有者，lsof 不可见）
_LINE_ROOT_LISTEN = (
    "tcp4       0      0  127.0.0.1.19090        *.*                    "
    "LISTEN                 0            0  131072  131072             "
    "kiss:30384  00180 00000006 0000000001a518f7 00000000 00000800"
)
_LINE_V6_LISTEN = (
    "tcp6       0      0  ::1.29090.              *.*                    "
    "LISTEN                 0            0  131072  131072             "
    "sing-box:999  00180"
)
_LINE_ESTABLISHED = (
    "tcp4       0      0  127.0.0.1.61673         127.0.0.1.19091       "
    "ESTABLISHED            0            0  131072  131072             "
    "qemu-syst:84333  00180"
)
_LINE_NO_OWNER = (
    "tcp4       0      0  127.0.0.1.19090        *.*                    "
    "LISTEN                 0            0  131072  131072"
)


def test_owner_pid_from_root_listen_line():
    assert _netstat_line_owner_pid(_LINE_ROOT_LISTEN, 19090) == 30384


def test_owner_pid_ipv6_and_other_port():
    assert _netstat_line_owner_pid(_LINE_V6_LISTEN, 29090) == 999
    # 端口不匹配 ⇒ None（不能把别的端口的持有者报进来）
    assert _netstat_line_owner_pid(_LINE_V6_LISTEN, 19090) is None


def test_established_line_not_reported():
    # 只看 LISTEN 行；ESTABLISHED 的本地端口不是「占口」证据
    assert _netstat_line_owner_pid(_LINE_ESTABLISHED, 61673) is None


def test_line_without_owner_column():
    # 无持有者列（极端裁剪行）⇒ None 而非抛错
    assert _netstat_line_owner_pid(_LINE_NO_OWNER, 19090) is None


def test_short_and_header_lines_ignored():
    assert _netstat_line_owner_pid("tcp4 0 0", 19090) is None
    assert _netstat_line_owner_pid("", 19090) is None
