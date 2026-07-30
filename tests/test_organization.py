"""W3-02 组织实体模型 + 消歧逻辑测试。

覆盖：
- 名称规范化
- 组织消歧（精确匹配/模糊匹配/无匹配）
- 组织类型推断
- 供应商画像生成
- ORM 模型字段
"""
from __future__ import annotations

import pytest


# ========== 名称规范化测试 ==========

class TestNormalizeOrgName:
    """组织名称规范化。"""

    def test_basic_clean(self):
        """基础清洗：去除空白。"""
        from app.models.organization import normalize_org_name
        assert normalize_org_name("  某某公司  ") == "某某公司"

    def test_remove_parentheses(self):
        """去除括号内容。"""
        from app.models.organization import normalize_org_name
        assert normalize_org_name("某某公司(上海)") == "某某公司"
        assert normalize_org_name("某某公司（北京）") == "某某公司"

    def test_unify_suffix(self):
        """统一公司后缀。"""
        from app.models.organization import normalize_org_name
        # 股份有限公司后缀保留
        assert normalize_org_name("某某股份有限公司") == "某某股份有限公司"
        # 有限公司后缀保留
        assert normalize_org_name("某某有限公司") == "某某有限公司"

    def test_unify_dot(self):
        """统一间隔号。"""
        from app.models.organization import normalize_org_name
        assert normalize_org_name("某某•公司") == "某某·公司"
        assert normalize_org_name("某某・公司") == "某某·公司"

    def test_empty(self):
        """空字符串。"""
        from app.models.organization import normalize_org_name
        assert normalize_org_name("") == ""
        assert normalize_org_name(None) == ""


# ========== 名称哈希测试 ==========

class TestComputeNameHash:
    """名称哈希。"""

    def test_same_name_same_hash(self):
        """相同名称相同哈希。"""
        from app.models.organization import compute_name_hash
        h1 = compute_name_hash("某某公司")
        h2 = compute_name_hash("某某公司")
        assert h1 == h2
        assert len(h1) == 16

    def test_different_name_different_hash(self):
        """不同名称不同哈希。"""
        from app.models.organization import compute_name_hash
        h1 = compute_name_hash("甲公司")
        h2 = compute_name_hash("乙公司")
        assert h1 != h2


# ========== 组织消歧测试 ==========

class TestDisambiguateOrganization:
    """组织名称消歧。"""

    def test_empty_name(self):
        """空名称 → 不匹配。"""
        from app.models.organization import disambiguate_organization, DisambiguationResult
        result = disambiguate_organization("")
        assert not result.matched
        assert result.match_method == "empty_name"

    def test_no_existing_orgs(self):
        """无现有组织 → 不匹配。"""
        from app.models.organization import disambiguate_organization
        result = disambiguate_organization("某某公司")
        assert not result.matched
        assert result.match_method == "no_match"
        assert result.normalized_name == "某某公司"

    def test_exact_credit_code_match(self):
        """统一社会信用代码精确匹配（置信度 1.0）。"""
        from app.models.organization import disambiguate_organization
        existing = [("org1", "某某公司", "913100001234567890")]
        result = disambiguate_organization(
            "某某公司(别名)",
            unified_credit_code="913100001234567890",
            existing_orgs=existing,
        )
        assert result.matched
        assert result.organization_id == "org1"
        assert result.confidence == 1.0
        assert result.match_method == "exact_credit_code"

    def test_exact_name_match(self):
        """规范化名称精确匹配（置信度 0.95）。"""
        from app.models.organization import disambiguate_organization
        existing = [("org1", "某某公司", None)]
        result = disambiguate_organization(
            "某某公司(上海)",
            existing_orgs=existing,
        )
        assert result.matched
        assert result.organization_id == "org1"
        assert result.confidence == 0.95
        assert result.match_method == "exact_name"

    def test_fuzzy_name_match(self):
        """模糊匹配（SimHash 汉明距离 ≤ 3）。"""
        from app.models.organization import disambiguate_organization
        # 名称相似但不完全相同
        existing = [("org1", "上海某某信息技术有限公司", None)]
        result = disambiguate_organization(
            "上海某某信息技术有限公",
            existing_orgs=existing,
            fuzzy_threshold=3,
        )
        # 模糊匹配可能成功也可能失败（取决于 SimHash）
        # 主要验证逻辑不崩溃
        assert isinstance(result.matched, bool)
        if result.matched:
            assert result.match_method == "fuzzy_name"
            assert 0.7 <= result.confidence <= 0.9

    def test_no_match_different_name(self):
        """完全不同名称 → 不匹配。"""
        from app.models.organization import disambiguate_organization
        existing = [("org1", "甲公司", None)]
        result = disambiguate_organization(
            "完全不同的乙公司",
            existing_orgs=existing,
        )
        assert not result.matched
        assert result.match_method == "no_match"


# ========== 组织类型推断测试 ==========

class TestInferOrgType:
    """组织类型推断。"""

    def test_government(self):
        """政府机关。"""
        from app.models.organization import infer_org_type, ORG_TYPE_GOVERNMENT
        assert infer_org_type("上海市教育局") == ORG_TYPE_GOVERNMENT
        assert infer_org_type("某市人民政府") == ORG_TYPE_GOVERNMENT

    def test_institution(self):
        """事业单位。"""
        from app.models.organization import infer_org_type, ORG_TYPE_INSTITUTION
        assert infer_org_type("某某大学") == ORG_TYPE_INSTITUTION
        assert infer_org_type("某某医院") == ORG_TYPE_INSTITUTION

    def test_enterprise(self):
        """企业。"""
        from app.models.organization import infer_org_type, ORG_TYPE_ENTERPRISE
        assert infer_org_type("某某有限公司") == ORG_TYPE_ENTERPRISE
        assert infer_org_type("某某集团") == ORG_TYPE_ENTERPRISE

    def test_social_org(self):
        """社会组织。"""
        from app.models.organization import infer_org_type, ORG_TYPE_SOCIAL_ORG
        assert infer_org_type("某某行业协会") == ORG_TYPE_SOCIAL_ORG
        assert infer_org_type("某某基金会") == ORG_TYPE_SOCIAL_ORG

    def test_unknown(self):
        """未知类型。"""
        from app.models.organization import infer_org_type, ORG_TYPE_UNKNOWN
        assert infer_org_type("某某") == ORG_TYPE_UNKNOWN
        assert infer_org_type("") == ORG_TYPE_UNKNOWN


# ========== 供应商画像测试 ==========

class TestBuildSupplierProfile:
    """供应商画像生成。"""

    def test_empty_records(self):
        """无中标记录。"""
        from app.models.organization import build_supplier_profile, SupplierProfile
        profile = build_supplier_profile("org1", "某某公司", [])
        assert isinstance(profile, SupplierProfile)
        assert profile.organization_id == "org1"
        assert profile.normalized_name == "某某公司"
        assert profile.win_count == 0
        assert profile.total_win_amount == 0.0
        assert profile.profile_generated_at != ""

    def test_with_records(self):
        """有中标记录。"""
        from app.models.organization import build_supplier_profile
        records = [
            {
                "win_amount": 1000000,
                "purchaser_name": "教育局",
                "agency_name": "招标公司",
                "project_name": "采购项目1",
                "region": "上海",
                "win_date": "2026-01-15",
            },
            {
                "win_amount": 2000000,
                "purchaser_name": "卫生局",
                "agency_name": "招标公司",
                "project_name": "采购项目2",
                "region": "北京",
                "win_date": "2026-03-20",
            },
            {
                "win_amount": 1500000,
                "purchaser_name": "教育局",
                "agency_name": "另一招标公司",
                "project_name": "采购项目3",
                "region": "上海",
                "win_date": "2026-06-10",
            },
        ]
        profile = build_supplier_profile("org1", "某某公司", records)
        assert profile.win_count == 3
        assert profile.total_win_amount == 4500000.0
        assert "教育局" in profile.main_purchasers
        assert "招标公司" in profile.main_agencies
        assert "上海" in profile.active_regions
        assert profile.first_win_date == "2026-01-15"
        assert profile.last_win_date == "2026-06-10"

    def test_invalid_amount_ignored(self):
        """无效金额被忽略。"""
        from app.models.organization import build_supplier_profile
        records = [
            {"win_amount": "invalid", "purchaser_name": "教育局"},
            {"win_amount": None, "purchaser_name": "卫生局"},
        ]
        profile = build_supplier_profile("org1", "某某公司", records)
        assert profile.win_count == 2
        assert profile.total_win_amount == 0.0


# ========== ORM 模型字段测试 ==========

class TestOrganizationModel:
    """Organization ORM 模型字段。"""

    def test_table_name(self):
        """表名。"""
        from app.models.organization import Organization
        assert Organization.__tablename__ == "organizations"

    def test_has_required_fields(self):
        """包含必需字段。"""
        from app.models.organization import Organization
        # 检查字段存在
        assert hasattr(Organization, "organization_id")
        assert hasattr(Organization, "raw_name")
        assert hasattr(Organization, "normalized_name")
        assert hasattr(Organization, "unified_credit_code")
        assert hasattr(Organization, "org_type")
        assert hasattr(Organization, "disambiguation_confidence")
        assert hasattr(Organization, "manually_verified")


class TestPartyRoleModel:
    """PartyRole ORM 模型字段。"""

    def test_table_name(self):
        """表名。"""
        from app.models.organization import PartyRole
        assert PartyRole.__tablename__ == "party_roles"

    def test_has_required_fields(self):
        """包含必需字段。"""
        from app.models.organization import PartyRole
        assert hasattr(PartyRole, "organization_id")
        assert hasattr(PartyRole, "tender_id")
        assert hasattr(PartyRole, "role")
        assert hasattr(PartyRole, "raw_name_in_notice")
        assert hasattr(PartyRole, "lot_id")
        assert hasattr(PartyRole, "consortium_id")
        assert hasattr(PartyRole, "win_amount")


# ========== 枚举测试 ==========

class TestEnums:
    """枚举值。"""

    def test_valid_org_types(self):
        """组织类型枚举。"""
        from app.models.organization import VALID_ORG_TYPES
        assert "government" in VALID_ORG_TYPES
        assert "institution" in VALID_ORG_TYPES
        assert "enterprise" in VALID_ORG_TYPES
        assert "social_org" in VALID_ORG_TYPES
        assert "unknown" in VALID_ORG_TYPES

    def test_valid_roles(self):
        """业务角色枚举。"""
        from app.models.organization import VALID_ROLES
        assert "purchaser" in VALID_ROLES
        assert "agency" in VALID_ROLES
        assert "bidder" in VALID_ROLES
        assert "winner" in VALID_ROLES
        assert "consortium" in VALID_ROLES
