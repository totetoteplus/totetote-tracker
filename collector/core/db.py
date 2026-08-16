"""Supabase接続と保存処理(Phase 3)。

service_role keyはこのモジュール以外(サーバー側コード)でのみ使用し、
ブラウザ等クライアントに渡さないこと。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

from core.errors import DatabaseError
from core.models import RunStats

load_dotenv()


@lru_cache(maxsize=1)
def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise DatabaseError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が未設定です。"
            "collector/.env を collector/.env.example からコピーして値を設定してください。"
        )

    # 前後の空白・引用符・改行はコピペ時に混入しやすいため自動で取り除く。
    # それでも不正な形式ならば、値そのものは出力せず形式面の情報だけを添えて失敗させる。
    stripped_url = url.strip().strip('"').strip("'")
    stripped_key = key.strip().strip('"').strip("'")
    if not stripped_url.startswith("https://"):
        raise DatabaseError(
            "SUPABASE_URL の形式が不正な可能性があります "
            f"(len={len(url)}, starts_with_https={stripped_url.startswith('https://')}). "
            "Project Settings > API の Project URL をそのまま設定してください。"
        )

    return create_client(stripped_url, stripped_key)


def upsert_shop(name: str, domain: str, official_url: str | None = None) -> str:
    """shops.domain をキーにupsertし、shops.id を返す。"""
    client = get_client()
    try:
        res = (
            client.table("shops")
            .upsert(
                {"name": name, "domain": domain, "official_url": official_url},
                on_conflict="domain",
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - supabase-py の例外型は多岐にわたるため包括的に捕捉
        raise DatabaseError(f"shops upsert failed for domain={domain}: {exc}") from exc

    if not res.data:
        raise DatabaseError(f"shops upsert returned no data for domain={domain}")
    return res.data[0]["id"]


def find_source_page(shop_id: str, url: str) -> dict[str, Any] | None:
    """(shop_id, url) が既に記録済みか調べる。あれば行を、無ければNoneを返す。"""
    client = get_client()
    try:
        res = (
            client.table("source_pages")
            .select("id, content_hash")
            .eq("shop_id", shop_id)
            .eq("url", url)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"source_pages lookup failed for url={url}: {exc}") from exc

    return res.data[0] if res.data else None


def insert_source_page(shop_id: str, url: str, page_type: str, content_hash: str) -> str:
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            client.table("source_pages")
            .insert(
                {
                    "shop_id": shop_id,
                    "url": url,
                    "page_type": page_type,
                    "content_hash": content_hash,
                    "last_checked_at": now,
                }
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"source_pages insert failed for url={url}: {exc}") from exc

    if not res.data:
        raise DatabaseError(f"source_pages insert returned no data for url={url}")
    return res.data[0]["id"]


def insert_match_candidate(
    raw_product_name: str,
    raw_data: dict[str, Any],
    source_page_id: str,
    confidence: float | None = None,
) -> str:
    """自動統合できない/未構造化のアイテムを候補として保存する。"""
    client = get_client()
    try:
        res = (
            client.table("product_match_candidates")
            .insert(
                {
                    "raw_product_name": raw_product_name,
                    "raw_data": raw_data,
                    "source_page_id": source_page_id,
                    "confidence": confidence,
                    "status": "pending",
                }
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"product_match_candidates insert failed: {exc}") from exc

    if not res.data:
        raise DatabaseError("product_match_candidates insert returned no data")
    return res.data[0]["id"]


def get_last_run_started_at(collector_key: str) -> datetime | None:
    """指定Collectorの直近実行開始時刻を返す(scheduler/run_due.py の期限判定用)。"""
    client = get_client()
    try:
        res = (
            client.table("collector_runs")
            .select("started_at")
            .eq("collector_key", collector_key)
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(
            f"collector_runs lookup failed for {collector_key}: {exc}"
        ) from exc

    if not res.data:
        return None
    return datetime.fromisoformat(res.data[0]["started_at"])


def record_run(stats: RunStats) -> None:
    """collector_runs へ1回分の実行サマリを記録する。"""
    client = get_client()
    try:
        client.table("collector_runs").insert(
            {
                "collector_key": stats.collector_key,
                "started_at": stats.started_at.isoformat(),
                "finished_at": stats.finished_at.isoformat() if stats.finished_at else None,
                "fetched_count": stats.fetched_count,
                "new_count": stats.new_count,
                "updated_count": stats.updated_count,
                "error_count": stats.error_count,
                "error_details": stats.error_details,
                "status": stats.status,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"collector_runs insert failed: {exc}") from exc


# --- Phase 7以降で拡張予定のインターフェース(構造化済みCollector向け) ---
#
# def upsert_product(item: CollectedItem) -> str: ...          # products.id を返す
# def upsert_listing(product_id: str, item: CollectedItem) -> str: ...
# def upsert_lottery(product_id: str, item: CollectedItem) -> str: ...
# def record_price_history(listing_id: str, item: CollectedItem) -> None: ...
