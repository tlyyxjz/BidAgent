"""SimHash 64 位内容指纹算法。

命题第 3 项硬要求：内容清洗去重。
- 把 core_content 转成 64 位指纹
- 汉明距离 ≤ 3 视为重复

工程规范：
- 纯函数，无副作用
- 支持中英文混合（结巴分词，失败时退化为字符 n-gram）
- 单条文本计算 < 50ms（10KB 以内）
- M-3 修复：调用方（tender_ingestor）用 asyncio.to_thread 包裹本模块同步函数
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Callable

from app.utils.logger import get_logger

logger = get_logger("simhash")

# 64 位 mask
MASK64 = (1 << 64) - 1

# 分词器：优先 jieba，失败退化到字符 2-gram
_tokenizer: Callable[[str], list[str]] | None = None

try:
    import jieba  # type: ignore[import-not-found]

    # m-2 修复：模块级 lambda 改为标准函数（PEP8 E731）
    def _jieba_tokenizer(text: str) -> list[str]:  # pragma: no cover: jieba 未安装,此分支不可达
        return [t for t in jieba.lcut(text) if t.strip()]  # pragma: no cover

    _tokenizer = _jieba_tokenizer  # pragma: no cover
    logger.info("simhash tokenizer: jieba")  # pragma: no cover
except ImportError:
    logger.warning("jieba 未安装，退化到字符 2-gram")


def _char_ngrams(text: str, n: int = 2) -> list[str]:
    """字符 n-gram 分词（jieba 不可用时的退化方案）。"""
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) < n:
        return [cleaned] if cleaned else []
    return [cleaned[i : i + n] for i in range(len(cleaned) - n + 1)]


def _tokenize(text: str) -> list[str]:
    """分词（jieba 优先，退化到字符 2-gram）。"""
    if not text or not text.strip():
        return []
    if _tokenizer is not None:
        try:
            tokens = _tokenizer(text)
            if tokens:
                return tokens
        except Exception as exc:  # noqa: BLE001
            logger.warning("jieba 分词失败，退化到 n-gram: %s", exc)
    return _char_ngrams(text, 2)


def _hash64(token: str) -> int:
    """把 token 哈希成 64 位整数（MD5 前 8 字节）。"""
    h = hashlib.md5(token.encode("utf-8")).digest()
    # 取前 8 字节作为 64 位整数
    return int.from_bytes(h[:8], byteorder="big", signed=False)


def compute_simhash(text: str) -> int:
    """计算文本的 64 位 SimHash 指纹。

    算法步骤：
    1. 分词得到 token 列表
    2. 每个 token 哈希成 64 位
    3. 对每一位统计权重和（位为 1 则 +1，为 0 则 -1）
    4. 最终每一位若和 > 0 则置 1，否则置 0

    Args:
        text: 输入文本（中文/英文混合）

    Returns:
        64 位 SimHash 指纹（int）
    """
    if not text or not text.strip():
        return 0

    tokens = _tokenize(text)
    if not tokens:
        return 0

    # 64 位权重累加数组
    weights = [0] * 64

    for token in tokens:
        h = _hash64(token)
        for i in range(64):
            if h & (1 << i):
                weights[i] += 1
            else:
                weights[i] -= 1

    # 生成最终指纹
    simhash = 0
    for i in range(64):
        if weights[i] > 0:
            simhash |= (1 << i)

    return simhash & MASK64


def hamming_distance(a: int, b: int) -> int:
    """计算两个 64 位 SimHash 的汉明距离。"""
    return bin((a ^ b) & MASK64).count("1")


def is_similar(a: int, b: int, threshold: int = 3) -> bool:
    """判断两个 SimHash 是否相似（汉明距离 ≤ 阈值）。"""
    return hamming_distance(a, b) <= threshold


def find_duplicate_in_iter(
    target: int,
    candidates: Iterable[tuple[int, int]],
    threshold: int = 3,
) -> tuple[int, int] | None:
    """在候选集合中找重复。

    Args:
        target: 待查 SimHash
        candidates: 可迭代对象，每个元素是 (id, simhash) 元组
        threshold: 汉明距离阈值

    Returns:
        匹配到的 (id, simhash) 元组，无匹配返回 None
    """
    if target == 0:
        return None
    for cand_id, cand_hash in candidates:
        if cand_hash == 0:
            continue
        if hamming_distance(target, cand_hash) <= threshold:
            return (cand_id, cand_hash)
    return None
