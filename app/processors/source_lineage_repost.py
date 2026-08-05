"""同源转载识别（从 source_lineage.py 拆分）。

包含：
1. 同源转载候选识别（SimHash 汉明距离 ≤ 3）
2. v4.1 §8.1 转载识别 10 项特征计算
3. 基于特征的转载关系判定
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.processors.simhash import compute_simhash, hamming_distance
from app.utils.logger import get_logger

logger = get_logger("source_lineage")


# ========== 同源转载候选识别 ==========

def find_repost_candidates(
    target_simhash: int,
    candidates: list[tuple[str, int, str]],
    threshold: int = 3,
) -> list[tuple[str, int, str]]:
    """在同源转载候选集合中查找匹配项。

    Args:
        target_simhash: 待查 SimHash
        candidates: 候选列表，每个元素是 (source_id, simhash, source_url)
        threshold: 汉明距离阈值（默认 3）

    Returns:
        匹配的候选列表（按汉明距离升序）
    """
    if target_simhash == 0:
        return []
    matched = []
    for source_id, cand_hash, source_url in candidates:
        if cand_hash == 0:
            continue
        dist = hamming_distance(target_simhash, cand_hash)
        if dist <= threshold:
            matched.append((source_id, cand_hash, source_url))
    matched.sort(key=lambda x: hamming_distance(target_simhash, x[1]))
    return matched


# ========== v4.1 §8.1 转载识别 10 项特征 ==========


@dataclass
class RepostFeatures:
    """v4.1 §8.1 转载识别 10 项特征。

    每项特征是一个 (match: bool, detail: str) 元组。
    match=True 表示该特征指向"同源转载"。
    """

    # 1. 原始链接特征（domain 匹配）
    domain_match: tuple[bool, str] = (False, "")
    # 2. 页面来源标注（含"转载"/"来源："等标记）
    repost_marker: tuple[bool, str] = (False, "")
    # 3. 项目编号一致
    project_identifier_match: tuple[bool, str] = (False, "")
    # 4. 公告类型一致
    notice_type_match: tuple[bool, str] = (False, "")
    # 5. 标题相似度高
    title_similarity: tuple[bool, str] = (False, "")
    # 6. 正文 SimHash 相似
    simhash_similar: tuple[bool, str] = (False, "")
    # 7. 发布主体一致
    publisher_match: tuple[bool, str] = (False, "")
    # 8. 发布时间关系（时间差合理）
    time_relation: tuple[bool, str] = (False, "")
    # 9. 附件链接一致
    attachment_link_match: tuple[bool, str] = (False, "")
    # 10. 正文中有上游来源说明
    upstream_source_mention: tuple[bool, str] = (False, "")

    def match_count(self) -> int:
        """匹配的特征数量。"""
        return sum(
            1
            for attr in [
                "domain_match",
                "repost_marker",
                "project_identifier_match",
                "notice_type_match",
                "title_similarity",
                "simhash_similar",
                "publisher_match",
                "time_relation",
                "attachment_link_match",
                "upstream_source_mention",
            ]
            if getattr(self, attr)[0]
        )

    def to_dict(self) -> dict:
        """转为字典。"""
        return {
            "domain_match": list(self.domain_match),
            "repost_marker": list(self.repost_marker),
            "project_identifier_match": list(self.project_identifier_match),
            "notice_type_match": list(self.notice_type_match),
            "title_similarity": list(self.title_similarity),
            "simhash_similar": list(self.simhash_similar),
            "publisher_match": list(self.publisher_match),
            "time_relation": list(self.time_relation),
            "attachment_link_match": list(self.attachment_link_match),
            "upstream_source_mention": list(self.upstream_source_mention),
            "match_count": self.match_count(),
        }


def _extract_domain_simple(url: str) -> str:
    """从 URL 提取域名（简化版）。"""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def _title_similarity(title_a: str, title_b: str) -> float:
    """计算两个标题的相似度（0.0-1.0）。

    使用字符级 Jaccard 相似度（不依赖 jieba）。
    """
    if not title_a or not title_b:
        return 0.0
    # 字符 2-gram 集合
    def to_bigrams(s: str) -> set[str]:
        s = s.strip()
        if len(s) < 2:
            return {s} if s else set()
        return {s[i : i + 2] for i in range(len(s) - 1)}

    bigrams_a = to_bigrams(title_a)
    bigrams_b = to_bigrams(title_b)
    if not bigrams_a or not bigrams_b:
        return 0.0
    intersection = bigrams_a & bigrams_b
    union = bigrams_a | bigrams_b
    return len(intersection) / len(union) if union else 0.0


def compute_repost_features(
    source_url_a: str,
    content_text_a: str,
    source_url_b: str,
    content_text_b: str = "",
    *,
    project_identifier_a: str = "",
    project_identifier_b: str = "",
    notice_type_a: str = "",
    notice_type_b: str = "",
    title_a: str = "",
    title_b: str = "",
    publisher_a: str = "",
    publisher_b: str = "",
    publish_time_a: str = "",
    publish_time_b: str = "",
    attachment_links_a: list[str] | None = None,
    attachment_links_b: list[str] | None = None,
) -> RepostFeatures:
    """计算 v4.1 §8.1 转载识别 10 项特征。

    Args:
        source_url_a: 来源 A 的 URL
        content_text_a: 来源 A 的正文
        source_url_b: 来源 B 的 URL
        content_text_b: 来源 B 的正文
        project_identifier_a/b: 项目编号
        notice_type_a/b: 公告类型
        title_a/b: 标题
        publisher_a/b: 发布主体
        publish_time_a/b: 发布时间（ISO 8601 或任意字符串）
        attachment_links_a/b: 附件链接列表

    Returns:
        RepostFeatures（10 项特征）
    """
    features = RepostFeatures()

    # 1. 原始链接（domain 匹配）
    domain_a = _extract_domain_simple(source_url_a)
    domain_b = _extract_domain_simple(source_url_b)
    if domain_a and domain_b:
        if domain_a == domain_b:
            features.domain_match = (True, f"域名一致: {domain_a}")
        elif domain_a.endswith(domain_b) or domain_b.endswith(domain_a):
            features.domain_match = (True, f"子域匹配: {domain_a} ~ {domain_b}")

    # 2. 页面来源标注
    repost_markers = ("转载", "来源：", "文章来源", "信息来源", "转自")
    has_marker_a = any(m in content_text_a for m in repost_markers) if content_text_a else False
    has_marker_b = any(m in content_text_b for m in repost_markers) if content_text_b else False
    if has_marker_a or has_marker_b:
        markers_found = [m for m in repost_markers if m in content_text_a or m in content_text_b]
        features.repost_marker = (True, f"含转载标记: {','.join(markers_found)}")

    # 3. 项目编号一致
    if project_identifier_a and project_identifier_b:
        if project_identifier_a == project_identifier_b:
            features.project_identifier_match = (True, f"项目编号一致: {project_identifier_a}")

    # 4. 公告类型一致
    if notice_type_a and notice_type_b:
        if notice_type_a == notice_type_b:
            features.notice_type_match = (True, f"公告类型一致: {notice_type_a}")

    # 5. 标题相似度
    if title_a and title_b:
        sim = _title_similarity(title_a, title_b)
        if sim >= 0.8:
            features.title_similarity = (True, f"标题相似度 {sim:.2f} ≥ 0.80")
        elif sim >= 0.6:
            features.title_similarity = (True, f"标题相似度 {sim:.2f} ≥ 0.60")

    # 6. 正文 SimHash 相似
    if content_text_a and content_text_b:
        simhash_a = compute_simhash(content_text_a)
        simhash_b = compute_simhash(content_text_b)
        distance = hamming_distance(simhash_a, simhash_b)
        if distance <= 3:
            features.simhash_similar = (True, f"SimHash 海明距离 {distance} ≤ 3")

    # 7. 发布主体一致
    if publisher_a and publisher_b:
        if publisher_a == publisher_b:
            features.publisher_match = (True, f"发布主体一致: {publisher_a}")

    # 8. 发布时间关系
    if publish_time_a and publish_time_b:
        # 简化判定：时间字符串前 10 位（日期部分）一致视为时间关系密切
        date_a = publish_time_a[:10] if len(publish_time_a) >= 10 else publish_time_a
        date_b = publish_time_b[:10] if len(publish_time_b) >= 10 else publish_time_b
        if date_a == date_b:
            features.time_relation = (True, f"发布日期一致: {date_a}")
        else:
            features.time_relation = (False, f"发布日期不同: {date_a} vs {date_b}")

    # 9. 附件链接一致
    if attachment_links_a and attachment_links_b:
        common_links = set(attachment_links_a) & set(attachment_links_b)
        if common_links:
            features.attachment_link_match = (True, f"共同附件链接 {len(common_links)} 个")

    # 10. 正文中有上游来源说明
    upstream_markers = ("转自", "来源：", "文章来源：", "信息来源：", "原文链接")
    has_upstream_a = any(m in content_text_a for m in upstream_markers) if content_text_a else False
    has_upstream_b = any(m in content_text_b for m in upstream_markers) if content_text_b else False
    if has_upstream_a or has_upstream_b:
        markers_found = [m for m in upstream_markers if m in content_text_a or m in content_text_b]
        features.upstream_source_mention = (True, f"含上游来源说明: {','.join(markers_found)}")

    return features


# 转载判定阈值
REPOST_MATCH_THRESHOLD = 3  # 至少 3 项特征匹配才判定为同源转载


def judge_repost_with_features(features: RepostFeatures) -> tuple[str, str]:
    """基于 10 项特征判定转载关系。

    Args:
        features: RepostFeatures（10 项特征）

    Returns:
        (judgment, reason)
        judgment 可能值:
        - "same_source_repost": 同源转载（match_count >= REPOST_MATCH_THRESHOLD 且 simhash_similar）
        - "likely_repost": 可能转载（match_count >= REPOST_MATCH_THRESHOLD 但 simhash 不相似）
        - "independent": 独立来源（match_count < REPOST_MATCH_THRESHOLD）
        - "unknown": 无法判定
    """
    count = features.match_count()

    if count >= REPOST_MATCH_THRESHOLD:
        if features.simhash_similar[0]:
            return "same_source_repost", f"{count} 项特征匹配且 SimHash 相似"
        return "likely_repost", f"{count} 项特征匹配但 SimHash 不相似"

    if count == 0:
        return "independent", "无特征匹配"

    return "independent", f"仅 {count} 项特征匹配（阈值 {REPOST_MATCH_THRESHOLD}）"
