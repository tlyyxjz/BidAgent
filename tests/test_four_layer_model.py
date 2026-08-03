"""四层实体模型测试（v4.1 第四章）。

覆盖：
- 6 个实体类的创建与字段验证
- 四层聚合关系链（Project → Notice → Source → Version）
- 数据迁移幂等性（Tender → 四层实体）
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.database import AsyncSessionLocal
from app.models.tender import Tender
from app.models.tender_project import (
    NoticeSource,
    NoticeVersion,
    TenderNotice,
    TenderProject,
)
from app.utils.data_migration import migrate_tender_to_four_layer

pytestmark = pytest.mark.asyncio


class TestFourLayerModel:
    """四层实体模型创建与关系测试。"""

    async def test_tender_project_creation(self):
        """TenderProject 创建：ULID 主键 + 必填字段 + 时间戳。"""
        project = TenderProject(
            canonical_name="测试采购项目",
            industry_category="goods",
            resolution_status="resolved",
        )
        async with AsyncSessionLocal() as db:
            db.add(project)
            await db.commit()
            await db.refresh(project)
            assert project.project_id is not None
            assert len(project.project_id) == 26  # ULID 26 字符
            assert project.canonical_name == "测试采购项目"
            assert project.industry_category == "goods"
            assert project.resolution_status == "resolved"
            assert project.created_at is not None
            assert project.updated_at is not None

    async def test_tender_notice_creation(self):
        """TenderNotice 创建：ULID 主键 + project_id 外键。"""
        project = TenderProject(
            canonical_name="公告测试项目",
            industry_category="service",
            resolution_status="resolved",
        )
        async with AsyncSessionLocal() as db:
            db.add(project)
            await db.flush()
            notice = TenderNotice(
                project_id=project.project_id,
                notice_type="tender",
                canonical_title="招标公告测试",
                status="active",
            )
            db.add(notice)
            await db.commit()
            await db.refresh(notice)
            assert notice.notice_id is not None
            assert len(notice.notice_id) == 26
            assert notice.project_id == project.project_id
            assert notice.notice_type == "tender"
            assert notice.status == "active"

    async def test_notice_source_creation(self):
        """NoticeSource 创建：ULID 主键 + notice_id 外键 + 索引字段。"""
        async with AsyncSessionLocal() as db:
            project = TenderProject(
                canonical_name="来源测试项目",
                industry_category="engineering",
                resolution_status="resolved",
            )
            db.add(project)
            await db.flush()
            notice = TenderNotice(
                project_id=project.project_id,
                notice_type="tender",
                canonical_title="来源测试公告",
                status="active",
            )
            db.add(notice)
            await db.flush()
            source = NoticeSource(
                notice_id=notice.notice_id,
                source_url="https://example.gov.cn/notice/source-1",
                source_platform="ccgp",
                platform_type="government",
                publication_role="original",
                source_quality="official_original",
                source_group="group-src-1",
            )
            db.add(source)
            await db.commit()
            await db.refresh(source)
            assert source.notice_source_id is not None
            assert len(source.notice_source_id) == 26
            assert source.notice_id == notice.notice_id
            assert source.source_url == "https://example.gov.cn/notice/source-1"

    async def test_notice_version_creation(self):
        """NoticeVersion 创建：ULID 主键 + notice_source_id 外键 + 内容指纹。"""
        async with AsyncSessionLocal() as db:
            project = TenderProject(
                canonical_name="版本测试项目",
                industry_category="goods",
                resolution_status="resolved",
            )
            db.add(project)
            await db.flush()
            notice = TenderNotice(
                project_id=project.project_id,
                notice_type="award",
                canonical_title="版本测试公告",
                status="active",
            )
            db.add(notice)
            await db.flush()
            source = NoticeSource(
                notice_id=notice.notice_id,
                source_url="https://example.gov.cn/notice/version-1",
                source_platform="ccgp",
                platform_type="government",
                publication_role="original",
                source_quality="official_original",
                source_group="group-v1",
            )
            db.add(source)
            await db.flush()
            version = NoticeVersion(
                notice_source_id=source.notice_source_id,
                http_status=200,
                content_sha256="a" * 64,
                raw_text_sha256="b" * 64,
                change_type="initial",
            )
            db.add(version)
            await db.commit()
            await db.refresh(version)
            assert version.version_id is not None
            assert len(version.version_id) == 26
            assert version.notice_source_id == source.notice_source_id
            assert version.change_type == "initial"
            assert version.http_status == 200
            assert len(version.content_sha256) == 64

    async def test_four_layer_relationship(self):
        """四层聚合关系：Project → Notice → Source → Version 链完整可查。"""
        async with AsyncSessionLocal() as db:
            project = TenderProject(
                canonical_name="四层关系测试项目",
                industry_category="goods",
                resolution_status="resolved",
            )
            db.add(project)
            await db.flush()

            notice = TenderNotice(
                project_id=project.project_id,
                notice_type="tender",
                canonical_title="四层关系测试公告",
                status="active",
            )
            db.add(notice)
            await db.flush()

            source = NoticeSource(
                notice_id=notice.notice_id,
                source_url="https://example.gov.cn/relationship/1",
                source_platform="ccgp",
                platform_type="government",
                publication_role="original",
                source_quality="official_original",
                source_group="rel-group",
            )
            db.add(source)
            await db.flush()

            version = NoticeVersion(
                notice_source_id=source.notice_source_id,
                http_status=200,
                content_sha256="c" * 64,
                raw_text_sha256="d" * 64,
                change_type="initial",
            )
            db.add(version)
            await db.commit()

            # 通过 version_id 反向查询整条关系链
            v = (await db.execute(
                select(NoticeVersion).where(
                    NoticeVersion.version_id == version.version_id
                )
            )).scalar_one()
            s = (await db.execute(
                select(NoticeSource).where(
                    NoticeSource.notice_source_id == v.notice_source_id
                )
            )).scalar_one()
            n = (await db.execute(
                select(TenderNotice).where(TenderNotice.notice_id == s.notice_id)
            )).scalar_one()
            p = (await db.execute(
                select(TenderProject).where(
                    TenderProject.project_id == n.project_id
                )
            )).scalar_one()

            assert p.canonical_name == "四层关系测试项目"
            assert n.canonical_title == "四层关系测试公告"
            assert n.project_id == p.project_id
            assert s.notice_id == n.notice_id
            assert v.notice_source_id == s.notice_source_id
            assert v.change_type == "initial"

    async def test_migration_idempotent(self):
        """数据迁移幂等性：重复运行不报错且不产生重复记录。"""
        # 准备一条 Tender 数据
        async with AsyncSessionLocal() as db:
            tender = Tender(
                project_name="迁移测试项目",
                source_url="https://example.gov.cn/migrate/idempotent-1",
                source_platform="ccgp",
                core_content="迁移测试核心内容",
                notice_type="tender",
            )
            db.add(tender)
            await db.commit()

        # 第一次迁移
        async with AsyncSessionLocal() as db:
            r1 = await migrate_tender_to_four_layer(db)
            assert r1["total"] == 1
            assert r1["migrated"] == 1
            assert r1["skipped"] == 0

        # 第二次迁移（幂等：应全部跳过）
        async with AsyncSessionLocal() as db:
            r2 = await migrate_tender_to_four_layer(db)
            assert r2["total"] == 1
            assert r2["migrated"] == 0
            assert r2["skipped"] == 1

        # 验证四层表各只有 1 条记录（无重复）
        async with AsyncSessionLocal() as db:
            projects = (await db.execute(
                select(TenderProject)
            )).scalars().all()
            notices = (await db.execute(
                select(TenderNotice)
            )).scalars().all()
            sources = (await db.execute(
                select(NoticeSource)
            )).scalars().all()
            versions = (await db.execute(
                select(NoticeVersion)
            )).scalars().all()
            assert len(projects) == 1
            assert len(notices) == 1
            assert len(sources) == 1
            assert len(versions) == 1
            # 验证关系链完整
            assert notices[0].project_id == projects[0].project_id
            assert sources[0].notice_id == notices[0].notice_id
            assert versions[0].notice_source_id == sources[0].notice_source_id
