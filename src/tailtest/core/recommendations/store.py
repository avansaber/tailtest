"""Persistent store for dismissed recommendations."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime
from pathlib import Path

from .schema import Recommendation

logger = logging.getLogger(__name__)

_DISMISSED_FILE = ".tailtest/dismissed.json"


class DismissalStore:
    """Reads and writes dismissals to .tailtest/dismissed.json.

    File format: a JSON object mapping recommendation id -> ISO-8601 datetime string.
    Each value is the `dismissed_until` timestamp.
    """

    def __init__(self, project_root: str | Path) -> None:
        self._path = Path(project_root) / _DISMISSED_FILE

    def load(self) -> dict[str, datetime]:
        """Return {rec_id: dismissed_until} from the store."""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            result: dict[str, datetime] = {}
            for rec_id, ts_str in raw.items():
                with contextlib.suppress(ValueError, TypeError):
                    result[rec_id] = datetime.fromisoformat(ts_str)
            return result
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read dismissal store: %s", exc)
            return {}

    def dismiss(self, rec_id: str, until: datetime) -> None:
        """Persist a dismissal for *rec_id*."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        current = self.load()
        current[rec_id] = until
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(
                    {k: v.isoformat() for k, v in current.items()},
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("Could not write dismissal store: %s", exc)
            tmp.unlink(missing_ok=True)

    def apply(self, recommendations: list[Recommendation]) -> list[Recommendation]:
        """Return recommendations with dismissed_until populated from the store."""
        dismissals = self.load()
        result = []
        for rec in recommendations:
            if rec.id in dismissals:
                result.append(rec.model_copy(update={"dismissed_until": dismissals[rec.id]}))
            else:
                result.append(rec)
        return result
