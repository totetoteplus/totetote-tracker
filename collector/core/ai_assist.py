"""AI補助モジュール（常時起動しない・独立モジュール）。

Collectorのメイン処理（fetch/parse/normalize/persist）はAIを使わない。
ここに置くのは、ルールベースでは対応しづらい下記のケースに限定した補助関数群。

  - extract_lottery_info: 自由記述のツイート本文等から、抽選/先着/受注販売の
    構造化情報（商品名・価格・応募期間・条件等）を抽出する
      （「抽選条件の文章解析」「複雑な文章から日時を抽出」に該当）

AI_ASSIST_API_KEY / ANTHROPIC_API_KEY が未設定の場合は全ての関数がNo-op
（Noneを返す）で動作し、Collectorの基本フローには影響しない。

捏造厳禁ルールを厳守するため、プロンプトでは「本文に明記されていない情報は
一切推測せず null にする」ことを明示的に指示している。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

EXTRACTION_MODEL = "claude-haiku-4-5"

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant": {
            "type": "boolean",
            "description": "抽選販売・先着販売・受注販売の告知として関連性があるか",
        },
        "product_name": {"type": ["string", "null"]},
        "sale_type": {
            "anyOf": [
                {"type": "string", "enum": ["lottery", "firstcome", "backorder"]},
                {"type": "null"},
            ]
        },
        "price": {"type": ["integer", "null"]},
        "application_start": {
            "type": ["string", "null"],
            "description": "ISO8601形式の日時 (例: 2026-08-17T11:00:00+09:00)。時刻不明なら日付のみ",
        },
        "application_end": {"type": ["string", "null"]},
        "result_date": {"type": ["string", "null"]},
        "release_date": {"type": ["string", "null"]},
        "conditions": {
            "type": ["string", "null"],
            "description": "応募条件・購入条件を本文の表現のまま短くまとめたもの",
        },
        "category": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": [
                        "pokemon", "yugioh", "onepiece", "dragonball", "beyblade",
                        "watch", "nike", "ichibankuji", "chiikawa", "livepocket",
                    ],
                },
                {"type": "null"},
            ],
            "description": "categoryが既に分かっている場合はnullのままでよい",
        },
    },
    "required": [
        "is_relevant", "product_name", "sale_type", "price",
        "application_start", "application_end", "result_date",
        "release_date", "conditions", "category",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """あなたは日本語の抽選販売・先着販売・受注販売トラッカーのデータ抽出アシスタントです。

与えられたテキスト（X/Twitterの投稿本文など）から、抽選/先着/受注販売の告知情報を抽出してください。

最重要ルール（絶対に守ること）:
- テキストに明記されていない情報は絶対に推測・補完しない。不明な項目は必ず null にする
- 価格・日付・条件などを「だいたいこれくらいだろう」で埋めない
- 抽選販売・先着販売・受注販売の告知として明確に関連性がない投稿（無関係な話題、他の抽選のRT、コラボ告知だが販売方式が書かれていない等）は is_relevant を false にする
- 日付に年が明記されていない場合、文脈上の基準日（渡された「本日の日付」）と同じ年と判断してよいが、月日だけで年をまたぐ可能性が疑われる場合は null にする
- sale_type は「抽選」なら lottery、「先着」「くじ」なら firstcome、「受注」「予約」なら backorder。判断できなければ null
"""


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AI_ASSIST_API_KEY")


def _ai_enabled() -> bool:
    return bool(_api_key())


def extract_lottery_info(text: str, reference_date: datetime | None = None) -> dict[str, Any] | None:
    """自由記述のテキストから抽選/先着/受注販売の構造化情報を抽出する。

    AI_ASSIST_API_KEY / ANTHROPIC_API_KEY が未設定の場合は None を返す
    (呼び出し側でルールベースのフォールバックを行うか、処理をスキップする)。
    """
    if not _ai_enabled():
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    ref_date = reference_date or datetime.now(timezone.utc)

    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": EXTRACTION_SCHEMA,
            }
        },
        messages=[
            {
                "role": "user",
                "content": (
                    f"本日の日付: {ref_date.strftime('%Y-%m-%d')}\n\n"
                    f"テキスト:\n{text}"
                ),
            }
        ],
    )

    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return None

    return json.loads(text_block.text)
