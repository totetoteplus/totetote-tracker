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
                        "text": text,
                        "created_at": tweet.get("createdAt"),
                        "url": url,
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
                    product_url=row["url"],
                    shop_name=f"X: @{row['handle']}",
                    notes=row["text"],
                    category=row["category"],
                    source_url=row["url"],
                    checked_at=checked_at,
                    source_method=SourceMethod.THIRD_PARTY_API,
                    needs_manual_review=(row["tier"] == "auto_judgment"),
                )
            )
        return items

    @staticmethod
    def _domain_per_account(item: CollectedItem) -> str:
        """X監視はアカウント単位で shops を分けたいので、handleをdomainに含める。"""
        parsed_url = urlparse(str(item.product_url))
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
