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

from core import ai_assist, db


class MatchDecision(str, Enum):
    MATCHED = "matched"          # 既存 products と一致
    NEW_PRODUCT = "new_product"  # 新規商品として登録してよい


@dataclass
class MatchResult:
    decision: MatchDecision
    product_id: str | None = None
    # MATCHED時のみ設定。既存商品の正式名称(表記ゆれ統一の基準名)。
    # lotteries.title を投稿ごとの表記ゆれではなく、この名称に揃えるために使う。
    matched_name: str | None = None


def normalize_name(raw_name: str) -> str:
    """全角/半角統一・空白圧縮・小文字化した比較用の正規化名を返す。"""
    text = unicodedata.normalize("NFKC", raw_name)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


_TOKEN_SEP_RE = re.compile(r"[「」『』()（）、,・/／\s]+")


def _significant_tokens(name: str) -> set[str]:
    """AI名寄せ判定を呼ぶ価値があるかの粗い事前フィルタ用トークン集合。

    3文字未満の断片(「BOX」等の非常に一般的な語含む)は誤検出が多いため除外する。
    """
    text = unicodedata.normalize("NFKC", name).lower()
    return {t for t in _TOKEN_SEP_RE.split(text) if len(t) >= 3}


def match_product(
    product_name: str,
    category: str | None = None,
    image_url: str | None = None,
) -> MatchResult:
    """商品名(+カテゴリ)からproducts.idを解決する。JANは対象外(v1)。

    image_url が渡され、かつ既存商品にまだ画像が無い場合のみ補完する
    (既にある画像を後発の低品質な画像で上書きしないため)。

    完全一致で見つからない場合、同カテゴリ内でキーワードが重なる既存商品が
    あればAIに「本当に同一商品か」を判定させる(第二段判定)。人気シリーズの
    発売告知は投稿ごとの表記ゆれ(括弧の種類・区切り記号・語順違い)で同じ
    商品が何行にも分裂しがちなため(例: 「30th CELEBRATION」関連投稿が
    34商品に分裂していた事例)。確信が持てない場合はAI側が必ずnullを返す
    前提なので、誤って別商品を統合してしまうリスクは小さい。
    """
    normalized = normalize_name(product_name)
    existing = db.find_product_by_normalized_name(normalized)

    if existing:
        if image_url and not existing.get("image_url"):
            db.update_product_image(existing["id"], image_url)
        return MatchResult(
            decision=MatchDecision.MATCHED,
            product_id=existing["id"],
            matched_name=existing.get("name"),
        )

    if category:
        new_tokens = _significant_tokens(product_name)
        candidates = [
            c
            for c in db.list_products_by_category(category)
            if new_tokens & _significant_tokens(c["name"])
        ]
        if candidates:
            idx = ai_assist.match_product_name(
                product_name, [c["name"] for c in candidates]
            )
            if idx is not None:
                matched = candidates[idx]
                if image_url and not matched.get("image_url"):
                    db.update_product_image(matched["id"], image_url)
                return MatchResult(
                    decision=MatchDecision.MATCHED,
                    product_id=matched["id"],
                    matched_name=matched.get("name"),
                )

    product_id = db.insert_product(
        name=product_name,
        normalized_name=normalized,
        category=category,
        image_url=image_url,
    )
    return MatchResult(decision=MatchDecision.NEW_PRODUCT, product_id=product_id)


#  投稿文中で使われがちな略称・通称を正式名称へ寄せるための対応表。
#  無いと同一店舗が略称違いでshopsに複数行できてしまう(例: 「ポケセンオンライン」
#  と「ポケモンセンターオンライン」)。キー・値はnormalize_name後の形で比較する。
SHOP_NAME_ALIASES: dict[str, str] = {
    normalize_name("ポケセンオンライン"): "ポケモンセンターオンライン",
}


def match_shop_by_name(shop_name: str) -> str:
    """本文中に明記された実施店舗名からshops.idを解決する(名称の完全一致のみ、v1)。

    X監視ではshops.domainを "x.com/{handle}" のように投稿アカウント単位で
    採番しているが、まとめ/転売系アカウントが本文中で別の実店舗名を挙げている
    場合は、その店舗名を正規化したものを合成domainキー("text:{normalized_name}")
    として使い、店舗名単位で寄せる。official_urlはテキストからは分からないため
    常にNone(捏造しないため推測はしない)。
    """
    normalized = normalize_name(shop_name)
    canonical_name = SHOP_NAME_ALIASES.get(normalized, shop_name)
    domain = f"text:{normalize_name(canonical_name)}"
    return db.upsert_shop(name=canonical_name, domain=domain, official_url=None)
