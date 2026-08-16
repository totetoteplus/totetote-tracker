"""AI補助モジュール（常時起動しない・独立モジュール）。

Collectorのメイン処理（fetch/parse/normalize/detect_changes）はAIを使わない。
ここに置くのは、ルールベースでは対応しづらい下記のケースに限定した補助関数群。

  - normalize_product_name: 表記揺れの強い商品名の正規化
  - extract_datetimes_from_text: 自由記述の条件文からの日時抽出
  - judge_product_identity: dedupe.pyの類似度判定だけでは閾値未満になるケースの補助判定
  - assist_on_structure_change: HTML構造変更でparse()が失敗した際の抽出補助

AI_ASSIST_API_KEY が未設定の場合は全ての関数がNo-op（入力をそのまま返す/Noneを返す）
で動作し、Collectorの基本フローには影響しない。実際のLLM呼び出しは必要になった
段階で個別に実装する（Phase 1時点では未接続）。
"""

from __future__ import annotations

import os


def _ai_enabled() -> bool:
    return bool(os.environ.get("AI_ASSIST_API_KEY"))


def normalize_product_name(raw_name: str) -> str:
    if not _ai_enabled():
        return raw_name.strip()
    raise NotImplementedError("AI補助による商品名正規化は未実装")


def extract_datetimes_from_text(text: str) -> dict[str, str]:
    if not _ai_enabled():
        return {}
    raise NotImplementedError("AI補助による日時抽出は未実装")


def judge_product_identity(name_a: str, name_b: str) -> float | None:
    """0.0〜1.0の同一性スコア、もしくは判定不能なら None を返す想定。"""
    if not _ai_enabled():
        return None
    raise NotImplementedError("AI補助による商品同一性判定は未実装")


def assist_on_structure_change(raw_html: str, expected_fields: list[str]) -> dict:
    if not _ai_enabled():
        return {}
    raise NotImplementedError("AI補助によるページ構造変更対応は未実装")
