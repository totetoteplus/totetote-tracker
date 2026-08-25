"""image_url が未設定の products に対し、紐づく lotteries の元ツイートを
再取得して商品画像を追加で取得する一回限りのバックフィル。

多くはimage_urls対応前に取り込まれた候補や、フル抽出時に画像パスが
発生しなかった(応募期間等が本文だけで解決した)候補に由来する。
ai_assist.extract_product_image (商品画像判定に特化した軽量呼び出し)を
使うため、フル抽出の再実行より安価に埋められる。

使い方:
    python scripts/backfill_product_images.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import ai_assist, db  # noqa: E402
from core.errors import CollectorError  # noqa: E402
from core.x_client import get_tweets_by_ids  # noqa: E402


def _tweet_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc not in ("x.com", "twitter.com"):
        return None
    parts = parsed.path.strip("/").split("/")
    return parts[-1] if len(parts) >= 3 and parts[-2] == "status" else None


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

    client = db.get_client()
    products = (
        client.table("products").select("id, name").is_("image_url", "null").execute()
    ).data
    print(f"画像未設定: {len(products)}件\n")

    product_ids = [p["id"] for p in products]
    lotteries = (
        client.table("lotteries")
        .select("product_id, title, source_url")
        .in_("product_id", product_ids)
        .execute()
    ).data

    lotteries_by_product: dict[str, list[dict]] = {}
    for lot in lotteries:
        lotteries_by_product.setdefault(lot["product_id"], []).append(lot)

    tweet_id_to_product: dict[str, str] = {}
    for pid, lots in lotteries_by_product.items():
        for lot in lots:
            tweet_id = _tweet_id_from_url(lot["source_url"])
            if tweet_id:
                tweet_id_to_product.setdefault(tweet_id, pid)
                break  # 1商品につき1件のツイートで試せば十分

    tweet_ids = list(tweet_id_to_product.keys())
    print(f"参照ツイート: {len(tweet_ids)}件を取得します...\n")

    try:
        tweets = get_tweets_by_ids(tweet_ids)
    except CollectorError as exc:
        print(f"[error] ツイート取得失敗: {exc}")
        return

    tweet_map = {t["id"]: t for t in tweets}
    print(f"取得できたツイート: {len(tweet_map)}/{len(tweet_ids)}件\n")

    updated = 0
    no_image_found = 0
    no_photo_attached = 0
    tweet_not_found = 0

    product_names = {p["id"]: p["name"] for p in products}

    for tweet_id, product_id in tweet_id_to_product.items():
        tweet = tweet_map.get(tweet_id)
        name = product_names[product_id][:40]
        if tweet is None:
            tweet_not_found += 1
            continue

        photo_urls = _photo_media_urls(tweet)
        if not photo_urls:
            no_photo_attached += 1
            continue

        image_url = ai_assist.extract_product_image(tweet.get("text", ""), photo_urls)
        if image_url:
            db.update_product_image(product_id, image_url)
            updated += 1
            print(f"  [image] {name} -> {image_url}")
        else:
            no_image_found += 1

    print(
        f"\n完了: updated={updated} no_image_found(添付はあるが商品写真なし)="
        f"{no_image_found} no_photo_attached(画像添付自体なし)={no_photo_attached} "
        f"tweet_not_found={tweet_not_found}"
    )


if __name__ == "__main__":
    main()
