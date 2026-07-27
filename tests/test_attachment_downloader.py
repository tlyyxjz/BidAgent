"""attachment_downloader 安全函数单元测试 (#13 修复)。

覆盖：
- _sanitize_filename：Windows 非法字符 / 路径穿越符 / basename 兜底 / 截断
- _is_path_safe：正常路径 / 路径遍历 / 绝对路径

注意：_sanitize_filename 实现先用 re.sub 把 \\ / : * ? " < > | 替换为 _，
再 replace(..,) 移除路径穿越符，最后 os.path.basename 兜底 + 截断到 100 字符。
因 re.sub 已把所有路径分隔符替换为 _，basename 实为兜底空操作。
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.processors.attachment_downloader import (
    _is_path_safe,
    _sanitize_filename,
)


class TestSanitizeFilename:
    def test_removes_windows_invalid_chars(self):
        r"""Windows 非法字符 \ / : * ? " < > | 替换为 _。"""
        raw = 'a\\b/c:d*e?f"g<h>i|j'
        # 每个非法字符都替换为 _
        assert _sanitize_filename(raw) == "a_b_c_d_e_f_g_h_i_j"

    def test_removes_path_traversal(self):
        """路径穿越符 .. 替换为空。"""
        assert _sanitize_filename("..hidden") == "hidden"

    def test_takes_basename(self):
        """路径形式输入被压平为单段文件名（分隔符替换 + basename 兜底）。

        实现先用 re.sub 把 / \\ 替换为 _，再 os.path.basename 兜底；
        /etc/passwd → _etc_passwd（无剩余分隔符，basename 原样保留）。
        """
        result = _sanitize_filename("/etc/passwd")
        # 结果不含路径分隔符，是一个扁平文件名
        assert "/" not in result
        assert "\\" not in result
        assert result == "_etc_passwd"

    def test_truncates_to_100_chars(self):
        """超长文件名截断到 100 字符。"""
        long_name = "x" * 150
        result = _sanitize_filename(long_name)
        assert len(result) == 100

    def test_normal_filename_unchanged(self):
        """正常文件名不变。"""
        assert _sanitize_filename("normal_file.pdf") == "normal_file.pdf"


class TestIsPathSafe:
    def test_safe_path_returns_true(self):
        """正常路径（在 ATTACHMENT_DIR 内）返回 (True, "")。"""
        root = Path(settings.ATTACHMENT_DIR).resolve()
        ok, reason = _is_path_safe(root / "tender1" / "file.pdf")
        assert ok is True
        assert reason == ""

    def test_path_traversal_returns_false(self):
        """路径遍历（.. 跳出 ATTACHMENT_DIR）返回 False。"""
        root = Path(settings.ATTACHMENT_DIR).resolve()
        ok, reason = _is_path_safe(root / ".." / ".." / "evil.txt")
        assert ok is False
        assert reason != ""

    def test_absolute_path_returns_false(self):
        """绝对路径（不在 ATTACHMENT_DIR 内）返回 False。"""
        ok, reason = _is_path_safe(Path(r"C:\Windows\system32\evil.dll"))
        assert ok is False
        assert reason != ""
