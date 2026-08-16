"""商品の重複排除・同一性判定。Phase 3で実装する。

設計方針（要求仕様より）:
  1. JANコードが一致すれば同一商品とみなす。
  2. JANがない場合、商品名＋メーカー／カテゴリ／ショップ等から類似度判定する。
  3. 色違い・BOX違い・セット商品などを誤統合しないよう、閾値未満は自動統合しない。
  4. 閾値未満・判断不能な場合は products へ直接書き込まず、
     product_match_candidates に候補として保存する（core.db.save_match_candidate）。

ルールベースの類似度判定で十分なケースが大半だが、型番違い・表記揺れなど
判断が難しいケースは core.ai_assist の補助判定に委譲できるようにする
（AI_ASSIST_API_KEY未設定時はルールベースのみで動作する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.models import CollectedItem


class MatchDecision(str, Enum):
    MATCHED = "matched"          # 既存 products と確定的に一致
    NEW_PRODUCT = "new_product"  # 新規商品として登録してよい
    CANDIDATE = "candidate"      # 自動判定できず product_match_candidates へ


@dataclass
class MatchResult:
    decision: MatchDecision
    product_id: str | None = None
    confidence: float | None = None


def match_product(item: CollectedItem) -> MatchResult:
    """CollectedItem に対応する products.id を解決する（Phase 3で実装）。"""

    raise NotImplementedError("core.dedupe.match_product は Phase 3 で実装する")
