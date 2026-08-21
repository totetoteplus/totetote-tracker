"""既存lotteriesの url(抽選ページ) / shop_id(実施店舗) を、元ツイートをID指定で
再取得して作り直す一回限りのバックフィル。

backfill_lottery_urls.py は「直近ツイート一覧」しか見られず、そこから
外れた古いツイートは復元できなかった。twitterapi.ioには特定ツイートIDを
指定して取得できるエンドポイント(/twitter/tweets)があることを確認できた
ため、こちらで全lotteriesの元ツイートを取得し直す。

あわせて、まとめ/転売系アカウント由来のlotteriesについては、本文に
投稿アカウントとは別の実施店舗名が明記されていればAI抽出で判定し、
shop_idをその店舗に付け替える(本文に無ければ投稿アカウント自身のまま)。

使い方:
    python scripts/backfill_lottery_sources.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import ai_assist, db, dedupe  # noqa: E402
from core.errors import CollectorError  # noqa: E402
from core.x_client import get_tweets_by_ids  # noqa: E402


def _tweet_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc not in ("x.com", "twitter.com"):
        return None
    parts = parsed.path.strip("/").split("/")
    return parts[-1] if len(parts) >= 3 and parts[-2] == "status" else None


def _first_url_entity(tweet: dict) -> str | None:
    urls = ((tweet.get("entities") or {}).get("urls")) or []
    for u in urls:
        expanded = u.get("expanded_url")
        if expanded:
            return expanded
    return None


def main() -> None:
    if not ai_assist._ai_enabled():  # noqa: SLF001
        print("[error] ANTHROPIC_API_KEY / AI_ASSIST_API_KEY が未設定です")
        return

    lotteries = db.list_lotteries_full()
    print(f"対象: {len(lotteries)}件\n")

    id_to_lottery: dict[str, list[dict]] = {}
    for row in lotteries:
        tweet_id = _tweet_id_from_url(row["source_url"])
        if tweet_id:
            id_to_lottery.setdefault(tweet_id, []).append(row)

    tweet_ids = list(id_to_lottery.keys())
    print(f"元ツイートID: {len(tweet_ids)}件を取得します...\n")

    try:
        tweets = get_tweets_by_ids(tweet_ids)
    except CollectorError as exc:
        print(f"[error] ツイート取得失敗: {exc}")
        return

    tweet_map = {t["id"]: t for t in tweets}
    print(f"取得できたツイート: {len(tweet_map)}/{len(tweet_ids)}件\n")

    url_updated = 0
    shop_updated = 0
    not_found = 0

    for tweet_id, rows in id_to_lottery.items():
        tweet = tweet_map.get(tweet_id)
        if tweet is None:
            not_found += len(rows)
            continue

        official_link = _first_url_entity(tweet)
        extracted = ai_assist.extract_lottery_info(tweet.get("text", ""))
        extracted_shop_name = (extracted or {}).get("shop_name")

        for row in rows:
            if official_link and row["url"] != official_link:
                db.update_lottery_url(row["id"], official_link)
                url_updated += 1
                print(f"  [url] {row['title'][:30]} -> {official_link}")

            if extracted_shop_name:
                shop_id = dedupe.match_shop_by_name(extracted_shop_name)
                if shop_id != row["shop_id"]:
                    db.update_lottery_shop(row["id"], shop_id)
                    shop_updated += 1
                    print(f"  [shop] {row['title'][:30]} -> {extracted_shop_name}")

    print(
        f"\n完了: url_updated={url_updated} shop_updated={shop_updated} "
        f"tweet_not_found={not_found}"
    )


if __name__ == "__main__":
    main()
