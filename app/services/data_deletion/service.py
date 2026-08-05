"""DataDeletionService: combines all deletion scope mixins (v4.1 sec 13.3)."""
from __future__ import annotations

from app.services.data_deletion._helpers import _DeletionHelpers
from app.services.data_deletion._notice_source_instance import (
    _NoticeSourceInstanceMixin,
)
from app.services.data_deletion._page_snapshot import _PageSnapshotMixin
from app.services.data_deletion._source_platform import _SourcePlatformMixin
from app.services.data_deletion._source_url import _SourceUrlMixin
from app.services.data_deletion._user_authorized_data import (
    _UserAuthorizedDataMixin,
)


class DataDeletionService(
    _SourceUrlMixin,
    _SourcePlatformMixin,
    _NoticeSourceInstanceMixin,
    _PageSnapshotMixin,
    _UserAuthorizedDataMixin,
    _DeletionHelpers,
):
    """Data deletion service implementing v4.1 sec 13.3.

    All deletion methods:
    - Accept an AsyncSession (caller manages transaction)
    - Return DeletionResult with counts of affected rows
    - Log audit record via _log_audit()
    - Never silently ignore errors (exceptions propagate to caller)
    """
