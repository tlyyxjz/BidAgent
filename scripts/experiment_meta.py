"""v4.1 §10.12 实验复现信息收集器。

收集 16 项实验复现信息，确保实验结果可追溯：
1. model_role: 模型角色（primary/glm/doubao）
2. provider: 服务商（deepseek/zhipu/doubao）
3. model_id: 完整模型标识
4. model_snapshot: 模型快照或 API 版本
5. request_time: 请求时间（ISO 8601）
6. temperature: 采样温度
7. top_p: nucleus sampling 参数
8. seed: 随机种子
9. request_id: 请求 ID（从 API 响应头获取）
10. prompt_hash: prompt 哈希
11. response_hash: 响应内容哈希
12. normalizer_version: 文本规范化规则版本
13. evidence_rule_version: 证据定位规则版本
14. display_rule_version: 展示等级规则版本
15. dataset_version: 数据集版本和哈希
16. code_commit: 代码提交版本（git commit hash）
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# 版本号常量（从各模块导入）
try:
    from app.processors.normalizer import NORMALIZER_VERSION
except ImportError:
    NORMALIZER_VERSION = "unknown"

try:
    from app.processors.evidence_locator import EVIDENCE_RULE_VERSION
except ImportError:
    EVIDENCE_RULE_VERSION = "evidence_locator_v1.0"

try:
    from app.processors.display_grade import DISPLAY_RULE_VERSION
except ImportError:
    DISPLAY_RULE_VERSION = "v0.1-calib"


@dataclass
class ExperimentMeta:
    """v4.1 §10.12 实验复现信息（16 项）。"""

    # ==== 模型信息（4 项）====
    model_role: str = "primary"  # primary/glm/doubao
    provider: str = "deepseek"  # deepseek/zhipu/doubao
    model_id: str = "unknown"  # 完整模型标识
    model_snapshot: Optional[str] = None  # 模型快照或 API 版本

    # ==== 请求参数（5 项）====
    request_time: str = ""  # ISO 8601 请求时间
    temperature: float = 0.0  # 采样温度
    top_p: float = 1.0  # nucleus sampling
    seed: Optional[int] = None  # 随机种子
    request_id: Optional[str] = None  # 请求 ID

    # ==== 哈希信息（2 项）====
    prompt_hash: str = ""  # prompt 哈希（已有）
    response_hash: Optional[str] = None  # 响应内容哈希

    # ==== 规则版本（3 项）====
    normalizer_version: str = NORMALIZER_VERSION
    evidence_rule_version: str = EVIDENCE_RULE_VERSION
    display_rule_version: str = DISPLAY_RULE_VERSION

    # ==== 数据与代码版本（2 项）====
    dataset_version: Optional[str] = None  # 数据集版本和哈希
    code_commit: Optional[str] = None  # git commit hash

    def to_dict(self) -> dict[str, Any]:
        """转为字典（用于 JSON 序列化）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperimentMeta":
        """从字典构造（忽略未知字段）。"""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


def compute_response_hash(response_content: str) -> str:
    """计算 LLM 响应内容的 SHA256 哈希（前 16 字符）。"""
    return hashlib.sha256(response_content.encode("utf-8")).hexdigest()[:16]


def compute_dataset_hash(dataset_path: Path | str) -> str:
    """计算数据集文件的 SHA256 哈希（前 16 字符）。"""
    path = Path(dataset_path)
    if not path.exists():
        return "unknown"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def get_git_commit() -> str:
    """获取当前 git commit hash（短）。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return "unknown"


def now_iso8601() -> str:
    """当前 UTC 时间 ISO 8601 格式。"""
    return datetime.now(timezone.utc).isoformat()


def collect_experiment_meta(
    model_id: str = "unknown",
    prompt_hash: str = "",
    response_content: Optional[str] = None,
    dataset_path: Optional[Path | str] = None,
    temperature: float = 0.0,
    top_p: float = 1.0,
    seed: Optional[int] = None,
    request_id: Optional[str] = None,
    model_role: str = "primary",
    provider: str = "deepseek",
    model_snapshot: Optional[str] = None,
) -> ExperimentMeta:
    """收集实验复现信息。

    Args:
        model_id: 完整模型标识
        prompt_hash: prompt 哈希
        response_content: LLM 响应内容（用于计算 response_hash）
        dataset_path: 数据集文件路径（用于计算 dataset_version）
        temperature: 采样温度
        top_p: nucleus sampling 参数
        seed: 随机种子
        request_id: 请求 ID
        model_role: 模型角色
        provider: 服务商
        model_snapshot: 模型快照或 API 版本

    Returns:
        ExperimentMeta 实例（16 项信息齐全）
    """
    return ExperimentMeta(
        model_role=model_role,
        provider=provider,
        model_id=model_id,
        model_snapshot=model_snapshot,
        request_time=now_iso8601(),
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        request_id=request_id,
        prompt_hash=prompt_hash,
        response_hash=compute_response_hash(response_content) if response_content else None,
        normalizer_version=NORMALIZER_VERSION,
        evidence_rule_version=EVIDENCE_RULE_VERSION,
        display_rule_version=DISPLAY_RULE_VERSION,
        dataset_version=compute_dataset_hash(dataset_path) if dataset_path else None,
        code_commit=get_git_commit(),
    )
