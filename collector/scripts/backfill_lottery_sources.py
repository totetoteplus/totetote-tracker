"""既存lotteriesの url(抽選ページ) / shop_id(実施店舗) / 応募期間等の日時を、
元ツイートをID指定で再取得してAI抽出をやり直す一回限りのバックフィル。

backfill_lottery_urls.py は「直近ツイート一覧」しか見られず、そこから
外れた古いツイートは復元できなかった。twitterapi.ioには特定ツイートIDを
指定して取得できるエンドポイント(/twitter/tweets)があることを確認できた
ため、こちらで全lotteriesの元ツイートを取得し直す。

あわせて、まとめ/転売系アカウント由来のlotteriesについては、本文に
投稿アカウントとは別の実施店舗名が明記されていればAI抽出で判定し、
shop_idをその店舗に付け替える(本文に無ければ投稿アカウント自身のまま)。

このスクリプトはai_assist.pyのプロンプト改善(「当選発表」「注文期限」を
application_start/application_endと混同しないようにする修正)を既存データ
にも反映するために追加した。改善前のプロンプトで抽出された行は、応募受付
期間ではなく当選発表・注文期限の日時が誤ってapplication_start/endに
入っているケースがあった(例: 応募は既に締切済みなのにstatus=openのまま
表示される不具合)。

あわせて、抽出結果のprice(本文に明記された抽選/販売価格。捏造なし)を
listings.retail_priceへも反映する(元々listings作成の仕組みが無く、
既存lotteries分は未反映のまま溜まっていたため)。

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
from core.status import compute_lottery_status  # noqa: E402
from core.x_client import get_tweets_by_ids  # noqa: E402

DATE_FIELDS = ("application_start", "application_end", "result_date", "release_date")


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


def _photo_media_urls(tweet: dict) -> list[str]:
    media = ((tweet.get("extendedEntities") or {}).get("media")) or []
    return [
        m["media_url_https"]
        for m in media
        if m.get("type") == "photo" and m.get("media_url_https")
    ]


def main() -> None:
    if not ai_assist._ai_enabled():  # noqa: SLF001
        print("[error] ANTHROPIC_API_KEY / AI_ASSIST_API_KEY が未設定です")
        return

    lotteries = db.list_lotteries_full()
    print(f"対象: {len(lotteries)}件\n")

    existing_listings = {
        (row["product_id"], row["shop_id"], row["url"]): row["retail_price"]
        for row in db.list_listings_basic()
    }

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
    dates_updated = 0
    status_updated = 0
    image_updated = 0
    price_updated = 0
    not_found = 0
    product_image_cache: dict[str, str | None] = {}

    for tweet_id, rows in id_to_lottery.items():
        tweet = tweet_map.get(tweet_id)
        if tweet is None:
            not_found += len(rows)
            continue

        official_link = _first_url_entity(tweet)
        extracted = (
            ai_assist.extract_lottery_info(
                tweet.get("text", ""), image_urls=_photo_media_urls(tweet)
            )
            or {}
        )
        extracted_shop_name = extracted.get("shop_name")

        for row in rows:
            if official_link and row["url"] != official_link:
                db.update_lottery_url(row["id"], official_link)
                url_updated += 1
                print(f"  [url] {row['title'][:30]} -> {official_link}")

            effective_shop_id = row["shop_id"]
            if extracted_shop_name:
                shop_id = dedupe.match_shop_by_name(extracted_shop_name)
                effective_shop_id = shop_id
                if shop_id != row["shop_id"]:
                    db.update_lottery_shop(row["id"], shop_id)
                    shop_updated += 1
                    print(f"  [shop] {row['title'][:30]} -> {extracted_shop_name}")

            date_changes = {
                f: extracted.get(f) for f in DATE_FIELDS if extracted.get(f) != row.get(f)
            }
            if date_changes:
                db.update_lottery_fields(row["id"], date_changes)
                dates_updated += 1
                print(f"  [dates] {row['title'][:30]} -> {date_changes}")

            new_start = date_changes.get("application_start", row.get("application_start"))
            new_end = date_changes.get("application_end", row.get("application_end"))
            new_status = compute_lottery_status(new_start, new_end)
            if new_status != row["status"]:
                db.update_lottery_status(row["id"], new_status)
                status_updated += 1
                print(f"  [status] {row['title'][:30]} {row['status']} -> {new_status}")

            product_image_url = extracted.get("product_image_url")
            product_id = row.get("product_id")
            if product_image_url and product_id:
                if product_id not in product_image_cache:
                    product_image_cache[product_id] = db.get_product_image(product_id)
                if not product_image_cache[product_id]:
                    db.update_product_image(product_id, product_image_url)
                    product_image_cache[product_id] = product_image_url
                    image_updated += 1
                    print(f"  [image] {row['title'][:30]} -> {product_image_url}")

            extracted_price = extracted.get("price")
            listing_url = official_link or row["url"] or row["source_url"]
            if extracted_price and listing_url:
                listing_key = (row["product_id"], effective_shop_id, listing_url)
                if existing_listings.get(listing_key) != extracted_price:
                    db.upsert_listing(
                        product_id=row["product_id"],
                        shop_id=effective_shop_id,
                        url=listing_url,
                        retail_price=extracted_price,
                    )
                    existing_listings[listing_key] = extracted_price
                    price_updated += 1
                    print(f"  [price] {row['title'][:30]} -> {extracted_price}円")

    print(
        f"\n完了: url_updated={url_updated} shop_updated={shop_updated} "
        f"dates_updated={dates_updated} status_updated={status_updated} "
        f"image_updated={image_updated} price_updated={price_updated} "
        f"tweet_not_found={not_found}"
    )


if __name__ == "__main__":
    main()
