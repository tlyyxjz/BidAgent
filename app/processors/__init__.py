"""数据处理模块：去重、解析和验证。"""

from app.processors.simhash import compute_simhash, hamming_distance, is_similar

__all__ = [
    "compute_simhash",
    "hamming_distance",
    "is_similar",
]
