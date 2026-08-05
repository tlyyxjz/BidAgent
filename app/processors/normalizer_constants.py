"""W2-02 文本规范化器常量。

从 normalizer.py 拆分而来，承载规范化规则版本号与正则/缓存常量。

规范化规则（normalizer_version = "1.0"）：
1. 全角字符转半角（Unicode NFKC 子集，仅处理常见招投标公告字符）
2. 英文字母大小写统一（转小写）
3. 连续空白压缩为单个空格（保留单个空格，但不删除行尾/行首空白后的换行）
4. 不改变语义内容
"""
from __future__ import annotations

import re
from typing import Dict

# 规范化器版本号（规则变更时必须升级）
NORMALIZER_VERSION = "1.0"

# 全角空格（U+3000）单独处理
_FULLWIDTH_SPACE = "\u3000"

# NFKC 规范化结果缓存（按字符缓存，避免重复 unicodedata.normalize 调用）
# 性能优化：20KB 全角文本场景下，unicodedata.normalize 是主要瓶颈
_NFKC_CACHE: Dict[str, str] = {}

# 连续空白正则（匹配 1 个或多个空白字符，包括空格、制表符、全角空格等，但不含换行）
_WHITESPACE_RUN = re.compile(r"[ \t\u3000\r\f\v]{2,}")

# 行首/行尾空白（去除每行首尾的空格和制表符，但保留换行）
_LINE_TRIM = re.compile(r"^[ \t\u3000]+|[ \t\u3000]+$", re.MULTILINE)
