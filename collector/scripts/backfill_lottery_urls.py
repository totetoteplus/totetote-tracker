"""既存の lotteries.url (現状は発見元のXツイートURLと同じ値) を、
可能な範囲でツイート内の外部リンク(公式抽選ページ等)に差し替える
一回限りのバックフィル。

twitterapi.ioの「最新ツイート取得」は直近分しか返さないため、
古いツイートは対象アカウントの直近ツイート一覧に含まれず更新できない
場合がある(その場合はXの投稿URLのままにする。捏造しないため推測はしない)。

使い方:
    python scripts/backfill_lottery_urls.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import db  # noqa: E402
from core.errors import CollectorError  # noqa: E402
from core.x_client import get_last_tweets  # noqa: E402


def _handle_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc not in ("x.com", "twitter.com"):
        return None
    parts = parsed.path.strip("/").split("/")
    return parts[0] if parts else None


def _first_url_entity(tweet: dict) -> str | None:
    urls = ((tweet.get("entities") or {}).get("urls")) or []
    for u in urls:
        expanded = u.get("expanded_url")
        if expanded:
            return expanded
    return None


def main() -> None:
    lotteries = db.list_lotteries_basic()
    # url がまだ source_url (Xの投稿URL)のままの行だけを対象にする
    targets = [r for r in lotteries if r["url"] == r["source_url"]]
    print(f"対象(url未更新): {len(targets)}件 / 全{len(lotteries)}件\n")

    tweets_cache: dict[str, list[dict]] = {}
    updated = 0
    not_found = 0
    skipped = 0

    for row in targets:
        handle = _handle_from_url(row["source_url"])
        if not handle:
            skipped += 1
            continue

        if handle not in tweets_cache:
            try:
                response = get_last_tweets(handle)
                tweets_cache[handle] = (response.get("data") or {}).get("tweets", [])
            except CollectorError as exc:
                print(f"  [skip] @{handle}: 取得失敗 ({exc})")
                tweets_cache[handle] = []

        match = next(
            (t for t in tweets_cache[handle] if t.get("url") == row["source_url"]),
            None,
        )
        if match is None:
            not_found += 1
            continue

        official_link = _first_url_entity(match)
        if not official_link:
            not_found += 1
            continue

        db.update_lottery_url(row["id"], official_link)
        updated += 1
        print(f"  [updated] @{handle}: {official_link}")

    print(
        f"\n完了: updated={updated} not_found(古い/リンクなし)={not_found} "
        f"skipped={skipped}"
    )


if __name__ == "__main__":
    main()
