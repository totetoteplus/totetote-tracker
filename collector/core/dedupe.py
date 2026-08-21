"""商品の重複排除・同一性判定。

設計方針（要求仕様より）:
  1. JANコードが一致すれば同一商品とみなす。
  2. JANがない場合、商品名＋メーカー／カテゴリ／ショップ等から類似度判定する。
  3. 色違い・BOX違い・セット商品などを誤統合しないよう、閾値未満は自動統合しない。
  4. 閾値未満・判断不能な場合は products へ直接書き込まず、
     product_match_candidates に候補として保存する（core.db.insert_match_candidate）。

v1実装: SNS等の情報源からはJANが取れないため、正規化済み商品名の完全一致
のみで判定する（類似度スコアリングは未実装）。一致しなければ常に新規商品
として扱う点に注意（色違い・型番違いの誤統合は避けられるが、逆に表記揺れ
による重複登録は許容している。dedupe精度向上は今後の課題）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from core import db


class MatchDecision(str, Enum):
    MATCHED = "matched"          # 既存 products と一致
    NEW_PRODUCT = "new_product"  # 新規商品として登録してよい


@dataclass
class MatchResult:
    decision: MatchDecision
    product_id: str | None = None


def normalize_name(raw_name: str) -> str:
    """全角/半角統一・空白圧縮・小文字化した比較用の正規化名を返す。"""
    text = unicodedata.normalize("NFKC", raw_name)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def match_product(product_name: str, category: str | None = None) -> MatchResult:
    """商品名(+カテゴリ)からproducts.idを解決する。JANは対象外(v1)。"""
    normalized = normalize_name(product_name)
    existing = db.find_product_by_normalized_name(normalized)

    if existing:
        return MatchResult(decision=MatchDecision.MATCHED, product_id=existing["id"])

    product_id = db.insert_product(
        name=product_name, normalized_name=normalized, category=category
    )
    return MatchResult(decision=MatchDecision.NEW_PRODUCT, product_id=product_id)


def match_shop_by_name(shop_name: str) -> str:
    """本文中に明記された実施店舗名からshops.idを解決する(名称の完全一致のみ、v1)。

    X監視ではshops.domainを "x.com/{handle}" のように投稿アカウント単位で
    採番しているが、まとめ/転売系アカウントが本文中で別の実店舗名を挙げている
    場合は、その店舗名を正規化したものを合成domainキー("text:{normalized_name}")
    として使い、店舗名単位で寄せる。official_urlはテキストからは分からないため
    常にNone(捏造しないため推測はしない)。
    """
    normalized = normalize_name(shop_name)
    domain = f"text:{normalized}"
    return db.upsert_shop(name=shop_name, domain=domain, official_url=None)
