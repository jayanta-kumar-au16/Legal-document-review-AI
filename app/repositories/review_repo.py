import threading
from typing import Any

from app.schemas.review import Finding, ReviewReport
from app.utils.clock import utc_now_iso


class InMemoryReviewRepository:
    """Prototype storage. Swap for SQLite/JSON later by keeping these three methods."""

    def __init__(self) -> None:
        self._items: dict[str, ReviewReport] = {}
        self._lock = threading.Lock()

    def add(self, report: ReviewReport) -> None:
        with self._lock:
            self._items[report.metadata.review_id] = report.model_copy(deep=True)

    def get(self, review_id: str) -> ReviewReport | None:
        with self._lock:
            report = self._items.get(review_id)
            return report.model_copy(deep=True) if report else None

    def update_finding(self, review_id: str, finding_id: str, changes: dict[str, Any]) -> Finding | None:
        """Apply `changes` (only 'status' / 'comment' keys) to one finding. None if not found."""
        with self._lock:
            report = self._items.get(review_id)
            if report is None:
                return None
            for finding in report.findings:
                if finding.finding_id == finding_id:
                    for key, value in changes.items():
                        setattr(finding, key, value)
                    finding.updated_at = utc_now_iso()
                    return finding.model_copy(deep=True)
            return None
