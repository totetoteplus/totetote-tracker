"""複数Collectorで共有する「保留候補として保存する」永続化ロジック。

products/listings/lotteries への直接反映に足る構造化(価格・応募期間等)が
まだできていないCollector向け。URL単位の既知チェックで重複を防ぎ、
未知のものだけを product_match_candidates へ保留候補として保存する。
"""

from __future__ import annotations

import hashlib
from typing import Callable
from urllib.parse import urlparse

from core import db
from core.errors import DatabaseError
from core.models import CollectedItem, PersistResult

DomainFn = Callable[[CollectedItem], str]


def _default_domain(item: CollectedItem) -> str:
    return urlparse(str(item.product_url)).netloc


def persist_as_candidates(
    items: list[CollectedItem],
    page_type: str,
    logger,
    domain_fn: DomainFn = _default_domain,
) -> PersistResult:
    """アイテムをURL単位の既知チェック付きで product_match_candidates へ保存する。

    shops は domain_fn(item) をキーにupsertする(既定はURLのドメインのみ。
    X監視のようにアカウント単位で店舗を分けたい場合は domain_fn を渡す)。
    既に source_pages に記録済みのURLはスキップする。
    """
    result = PersistResult()
    shop_id_cache: dict[str, str] = {}

    for item in items:
        try:
            domain = domain_fn(item)

            if domain not in shop_id_cache:
                shop_id_cache[domain] = db.upsert_shop(
                    name=item.shop_name, domain=domain
                )
            shop_id = shop_id_cache[domain]

            source_url = str(item.source_url)
            if db.find_source_page(shop_id, source_url):
                result.skipped_count += 1
                continue

            content_hash = hashlib.sha256(
                f"{item.product_name}|{item.notes or ''}".encode("utf-8")
            ).hexdigest()
            source_page_id = db.insert_source_page(
                shop_id, source_url, page_type=page_type, content_hash=content_hash
            )
            db.insert_match_candidate(
                raw_product_name=item.product_name,
                raw_data=item.model_dump(mode="json"),
                source_page_id=source_page_id,
            )
            result.new_count += 1
        except DatabaseError as exc:
            logger.error("persist failed for %s: %s", item.source_url, exc)
            result.errors.append(str(exc))

    return result
