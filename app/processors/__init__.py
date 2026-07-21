"""数据处理模块：附件下载、去重、解析和金融分析。"""

from app.processors.boq_engine import BOQReport, analyze_boq
from app.processors.risk_engine import RiskReport, analyze_risk, analyze_risk_engine
from app.processors.simhash import compute_simhash, hamming_distance, is_similar

__all__ = [
    "BOQReport",
    "RiskReport",
    "analyze_boq",
    "analyze_risk",
    "analyze_risk_engine",
    "compute_simhash",
    "hamming_distance",
    "is_similar",
]
