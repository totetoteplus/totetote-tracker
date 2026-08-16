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


# ============================================================
# 候補の構造化・昇格（AI補助抽出の結果をproducts/listings/lotteriesへ反映）
# ============================================================


def list_pending_candidates(limit: int = 20) -> list[dict[str, Any]]:
    """status='pending' の候補を古い順に取得する。"""
    return list_candidates_by_status("pending", limit=limit)


def list_candidates_by_status(status: str, limit: int = 20) -> list[dict[str, Any]]:
    """指定statusの候補を古い順に取得する。"""
    client = get_client()
    try:
        res = (
            client.table("product_match_candidates")
            .select("id, raw_product_name, raw_data")
            .eq("status", status)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"product_match_candidates lookup failed: {exc}") from exc

    return res.data or []


def update_candidate_status(
    candidate_id: str,
    status: str,
    candidate_product_id: str | None = None,
) -> None:
    client = get_client()
    payload: dict[str, Any] = {"status": status}
    if candidate_product_id is not None:
        payload["candidate_product_id"] = candidate_product_id
    try:
        client.table("product_match_candidates").update(payload).eq(
            "id", candidate_id
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(
            f"product_match_candidates update failed for id={candidate_id}: {exc}"
        ) from exc


def update_candidate_extraction(
    candidate_id: str,
    raw_data: dict[str, Any],
    status: str,
    extracted: dict[str, Any],
) -> None:
    """AI抽出結果を raw_data.extracted にマージして保存する(自動公開はしない)。

    auto_judgment層(needs_manual_review=True)の候補向け。
    構造化はできたが、公式リンク・カテゴリ確認が済むまで
    products/listings/lotteries には反映しない。
    """
    client = get_client()
    merged = {**raw_data, "extracted": extracted}
    try:
        client.table("product_match_candidates").update(
            {"raw_data": merged, "status": status}
        ).eq("id", candidate_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(
            f"product_match_candidates extraction update failed for id={candidate_id}: {exc}"
        ) from exc


def find_product_by_normalized_name(normalized_name: str) -> dict[str, Any] | None:
    """簡易dedupe(v1): 正規化済み商品名の完全一致で既存商品を探す。

    JANが取れない情報源(SNS等)向けの粗い実装。色違い・BOX違い等の誤統合を
    避けるため、今のところ「完全一致のみ」に留め、類似度判定は将来の課題とする。
    """
    client = get_client()
    try:
        res = (
            client.table("products")
            .select("id")
            .eq("normalized_name", normalized_name)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"products lookup failed for name={normalized_name}: {exc}") from exc

    return res.data[0] if res.data else None


def insert_product(
    name: str, normalized_name: str, category: str | None = None
) -> str:
    client = get_client()
    try:
        res = (
            client.table("products")
            .insert(
                {"name": name, "normalized_name": normalized_name, "category": category}
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"products insert failed for name={name}: {exc}") from exc

    if not res.data:
        raise DatabaseError(f"products insert returned no data for name={name}")
    return res.data[0]["id"]


def insert_lottery(
    product_id: str,
    shop_id: str,
    title: str,
    sale_type: str | None = None,
    url: str | None = None,
    application_start: str | None = None,
    application_end: str | None = None,
    result_date: str | None = None,
    release_date: str | None = None,
    conditions: str | None = None,
    status: str = "unknown",
    source_url: str | None = None,
) -> str:
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            client.table("lotteries")
            .insert(
                {
                    "product_id": product_id,
                    "shop_id": shop_id,
                    "title": title,
                    "sale_type": sale_type,
                    "url": url,
                    "application_start": application_start,
                    "application_end": application_end,
                    "result_date": result_date,
                    "release_date": release_date,
                    "conditions": conditions,
                    "status": status,
                    "source_url": source_url,
                    "last_checked_at": now,
                }
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"lotteries insert failed for title={title}: {exc}") from exc

    if not res.data:
        raise DatabaseError(f"lotteries insert returned no data for title={title}")
    return res.data[0]["id"]
