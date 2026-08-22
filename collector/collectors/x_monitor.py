"""X(Twitter)監視 Collector（twitterapi.io経由、Phase 2）。

複数アカウントを横断して監視するため、他サイトのCollectorのように
1アカウント=1ファイルにはせず、config/x_accounts.yaml で対象アカウントを
一覧管理し、本Collector1つでまとめて処理する
（取得方式・パース方式が全アカウント共通のため）。

tier: auto_judgment のアカウントから得たツイートは、公式リンク・カテゴリの
確認が済むまで CollectedItem.needs_manual_review=True とする。Phase 3以降で
products/listings/lotteries へ直接反映せず、product_match_candidates 経由の
保留扱いにする想定。

ツイート本文からの価格・応募期間等の構造化抽出はここでは行わない。
自由文からの日時抽出・条件解析は core.ai_assist に委譲する（Phase 7以降で拡張）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

from collectors.base import BaseCollector
from core.errors import CollectorError
from core.models import CollectedItem, PersistResult, SourceMethod
from core.staging import persist_as_candidates
from core.x_client import get_last_tweets

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "x_accounts.yaml"


def _load_accounts() -> list[dict]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["accounts"]


def _parse_created_at(value: str | None) -> datetime | None:
    """Twitter形式の日時文字列 ("Tue Dec 10 07:00:30 +0000 2024") をdatetimeへ。"""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _first_url_entity(tweet: dict) -> str | None:
    """ツイート本文中の外部リンク(t.co展開後)の最初の1件を返す。無ければNone。"""
    urls = ((tweet.get("entities") or {}).get("urls")) or []
    for u in urls:
        expanded = u.get("expanded_url")
        if expanded:
            return expanded
    return None


def _photo_media_urls(tweet: dict) -> list[str]:
    """ツイート添付の画像(告知カード画像等)URLを返す。動画/GIFは対象外。"""
    media = ((tweet.get("extendedEntities") or {}).get("media")) or []
    return [
        m["media_url_https"]
        for m in media
        if m.get("type") == "photo" and m.get("media_url_https")
    ]


def _author_name(tweet: dict) -> str | None:
    return ((tweet.get("author") or {}).get("name")) or None


def _author_bio_url(tweet: dict) -> str | None:
    """プロフィール欄に設定された公式サイトリンク(あれば)を返す。"""
    author = tweet.get("author") or {}
    bio = author.get("profile_bio") or {}
    urls = (((bio.get("entities") or {}).get("url")) or {}).get("urls") or []
    for u in urls:
        expanded = u.get("expanded_url")
        if expanded:
            return expanded
    return None


class XMonitorCollector(BaseCollector):
    source_key = "x_monitor"
    display_name = "X監視 (twitterapi.io)"

    def fetch(self) -> list[dict]:
        accounts = _load_accounts()
        results: list[dict] = []
        for account in accounts:
            try:
                response = get_last_tweets(account["handle"])
                # 実レスポンスは {status, code, msg, data: {pin_tweet, tweets: [...]}, ...}
                # (docs記載の {tweets: [...]} 直下構造とは異なることを実APIで確認済み)
                tweets = (response.get("data") or {}).get("tweets", [])
                results.append({"account": account, "tweets": tweets})
            except CollectorError as exc:
                # 1アカウントの失敗で全体を止めない
                self.logger.warning("failed to fetch @%s: %s", account["handle"], exc)
                results.append({"account": account, "tweets": []})
        return results

    def parse(self, raw: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for entry in raw:
            account = entry["account"]
            for tweet in entry["tweets"]:
                text = (tweet.get("text") or "").strip()
                url = tweet.get("url")
                if not text or not url:
                    continue
                rows.append(
                    {
                        "handle": account["handle"],
                        "category": account.get("category"),
                        "tier": account.get("tier", "auto_judgment"),
                        "official_multi_category": account.get(
                            "official_multi_category", False
                        ),
                        "text": text,
                        "created_at": tweet.get("createdAt"),
                        "url": url,
                        "official_link": _first_url_entity(tweet),
                        "author_name": _author_name(tweet),
                        "author_bio_url": _author_bio_url(tweet),
                        "image_urls": _photo_media_urls(tweet),
                    }
                )
        return rows

    def normalize(self, parsed: list[dict]) -> list[CollectedItem]:
        checked_at = datetime.now(timezone.utc)
        items: list[CollectedItem] = []
        for row in parsed:
            items.append(
                CollectedItem(
                    # ツイート本文自体は商品名として確定していないため、
                    # あくまで一次スクリーニング用の暫定表示名として先頭80文字を使う。
                    product_name=row["text"][:80],
                    # ツイート内に外部リンク(entities.urls)があれば実際の抽選/告知ページと
                    # みなして使う。無ければ暫定的にツイート自体のURLをそのまま使う
                    # (捏造厳禁のため、無いものをAIに推測させたりはしない)。
                    product_url=row["official_link"] or row["url"],
                    # 表示名はXアカウントの表示名(プロフィール名)をそのまま使う
                    # ("X: @handle"のような便宜的な名前より実店舗/実運営名に近い)。
                    shop_name=row["author_name"] or f"X: @{row['handle']}",
                    shop_official_url=row["author_bio_url"],
                    image_urls=row["image_urls"],
                    notes=row["text"],
                    category=row["category"],
                    # 発見元(ツイート自体)のURLは常にこちらへ固定する。
                    source_url=row["url"],
                    checked_at=checked_at,
                    source_method=SourceMethod.THIRD_PARTY_API,
                    needs_manual_review=(row["tier"] == "auto_judgment"),
                    promote_if_category_known=row["official_multi_category"],
                )
            )
        return items

    @staticmethod
    def _domain_per_account(item: CollectedItem) -> str:
        """X監視はアカウント単位で shops を分けたいので、handleをdomainに含める。

        product_url はツイート内の外部リンクに差し替わっている場合があるため、
        アカウントを一意に特定できる source_url (ツイート自体のURL) を基準にする。
        """
        parsed_url = urlparse(str(item.source_url))
        handle = parsed_url.path.strip("/").split("/")[0]
        return f"{parsed_url.netloc}/{handle}"

    def persist(self, current: list[CollectedItem]) -> PersistResult:
        """ツイートURL単位で既知チェックし、未知のものだけ候補として保存する。

        products/listings/lotteries には書き込まない
        (product_name がツイート本文由来の暫定値であり、価格・応募期間等の
        構造化がまだ済んでいないため)。confirmed/auto_judgment の別は
        raw_data にそのまま残し、将来の「候補→正式商品への昇格」処理で使う。
        """
        return persist_as_candidates(
            current,
            page_type="x_post",
            logger=self.logger,
            domain_fn=self._domain_per_account,
        )
