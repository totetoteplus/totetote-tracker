"""既存の shops (X監視由来、domain='x.com/{handle}') の name/official_url を
Xアカウントのプロフィール名・プロフィールURLで更新する一回限りのバックフィル。

「shops.name を X: @handle ではなく実店舗/実運営名にする」変更をコード側で
入れた後、既存行にも反映するためのスクリプト。アカウントごとに1回だけ
twitterapi.io を呼び出す(ツイート本文は使わずauthor情報だけを利用する)。

使い方:
    python scripts/backfill_shop_info.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import db  # noqa: E402
from core.errors import CollectorError  # noqa: E402
from core.x_client import get_last_tweets  # noqa: E402


def _author_name(tweet: dict) -> str | None:
    return ((tweet.get("author") or {}).get("name")) or None


def _author_bio_url(tweet: dict) -> str | None:
    author = tweet.get("author") or {}
    bio = author.get("profile_bio") or {}
    urls = (((bio.get("entities") or {}).get("url")) or {}).get("urls") or []
    for u in urls:
        expanded = u.get("expanded_url")
        if expanded:
            return expanded
    return None


def main() -> None:
    client = db.get_client()
    res = client.table("shops").select("id, name, domain, official_url").execute()
    shops = res.data or []

    updated = 0
    skipped = 0

    for shop in shops:
        domain = shop["domain"]
        if not domain.startswith("x.com/"):
            skipped += 1
            continue
        handle = domain.split("/", 1)[1]

        try:
            response = get_last_tweets(handle)
        except CollectorError as exc:
            print(f"  [skip] @{handle}: 取得失敗 ({exc})")
            skipped += 1
            continue

        tweets = (response.get("data") or {}).get("tweets", [])
        if not tweets:
            print(f"  [skip] @{handle}: ツイートなし")
            skipped += 1
            continue

        author_name = _author_name(tweets[0])
        bio_url = _author_bio_url(tweets[0])

        if not author_name:
            print(f"  [skip] @{handle}: author名が取得できず")
            skipped += 1
            continue

        db.upsert_shop(name=author_name, domain=domain, official_url=bio_url)
        updated += 1
        print(f"  [updated] @{handle}: name={author_name!r} official_url={bio_url!r}")

    print(f"\n完了: updated={updated} skipped={skipped} total={len(shops)}")


if __name__ == "__main__":
    main()
