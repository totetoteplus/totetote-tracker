"""lotteries.status (応募受付状況) の判定ロジック。

CLAUDE.md記載の定義に合わせる:
  open   -> 受付中 (application_start <= 現在 <= application_end、
            またはapplication_endが不明で開始済み)
  soon   -> 受付前 (現在 < application_start)
  closed -> 受付終了 (application_end < 現在)
  unknown -> 要確認・不定期 (開始/終了日時が判定に足りない)

application_start/application_end が両方ともnullの場合は
判定材料がないため常に unknown のままとする(捏造厳禁ルールに従い、
日付を推測で埋めない)。
"""

from __future__ import annotations

from datetime import datetime, timezone


def compute_lottery_status(
    application_start: datetime | str | None,
    application_end: datetime | str | None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    start = _as_datetime(application_start)
    end = _as_datetime(application_end)

    if end is not None and now > end:
        return "closed"
    if start is not None and now < start:
        return "soon"
    if start is not None:
        # 開始済みで、終了日時が不明 or 終了前
        return "open"
    if end is not None:
        # 開始日時は不明だが終了前 = 受付中とみなす
        return "open"
    return "unknown"


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
