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
        "shop_name": {
            "type": ["string", "null"],
            "description": (
                "抽選/販売を実施している実店舗・実運営者の名称が、"
                "投稿アカウント自身とは別の名前として本文中に明記されている場合のみ、"
                "その名称をそのまま返す（例: まとめ/転売系アカウントが「竜のしっぽにて抽選販売受付開始」"
                "のように別の店舗名を挙げているケース）。"
                "本文が単一の実施店舗を明確に指していない場合"
                "（複数店舗の一覧、店舗名の記載なし、投稿アカウント自身が実施店舗の場合等）はnull"
            ),
        },
        "sale_type": {
            "anyOf": [
                {"type": "string", "enum": ["lottery", "firstcome", "backorder"]},
                {"type": "null"},
            ]
        },
        "price": {"type": ["integer", "null"]},
        "application_start": {
            "type": ["string", "null"],
            "description": (
                "応募(抽選申込)の受付が始まる日時。ISO8601形式 "
                "(例: 2026-08-17T11:00:00+09:00)。時刻不明なら日付のみ。"
                "「当選発表」「注文期限」「購入期限」等、応募受付とは別のイベントの"
                "日時をここに入れない"
            ),
        },
        "application_end": {
            "type": ["string", "null"],
            "description": (
                "応募(抽選申込)の受付が終わる日時。「当選発表日時」(result_date)や"
                "「当選者向けの注文期限・購入期限」とは別物であり、混同しないこと。"
                "本文に応募受付終了の日時が書かれていなければnull"
                "(注文期限しか書かれていない投稿は多くの場合、応募自体は既に締め切られた"
                "後の当選者向け案内である)"
            ),
        },
        "result_date": {
            "type": ["string", "null"],
            "description": "当選発表・抽選結果発表の日時",
        },
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
        "is_relevant", "product_name", "shop_name", "sale_type", "price",
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
- pokemon/yugioh/onepiece/dragonball(トレーディングカードゲーム)カテゴリにおいて、対象商品が
  カード本体(拡張パック/BOX/スターター・ストラクチャーデッキ/シングルカード等)ではなく、
  プロテクター・スリーブ・デッキケース・プレイマット・カードファイル・バインダー・収納ポーチ等の
  「カードゲーム関連グッズ(付属品)」のみである場合は is_relevant を false にする
  (ichibankuji/chiikawa/watch/nike/beyblade等、商品自体がグッズであるカテゴリには適用しない)
- 日付に年が明記されていない場合、文脈上の基準日（渡された「本日の日付」）と同じ年と判断してよいが、月日だけで年をまたぐ可能性が疑われる場合は null にする
- sale_type は「抽選」なら lottery、「先着」「くじ」なら firstcome、「受注」「予約」なら backorder。判断できなければ null
- shop_name は、本文中に投稿アカウント自身とは異なる実施店舗名が明記されている場合のみ抽出する。
  店舗名を投稿アカウント名から推測したり、一般的な知識で補ったりしない
- 日時には複数の種類があり、絶対に混同しないこと:
    - application_start/application_end = 応募(抽選申込)の受付期間
    - result_date = 当選発表・抽選結果発表の日時
    - 当選者向けの「注文期限」「購入期限」「受け取り期限」は上記いずれにも該当しないため、
      application_start/application_end/result_dateのどれにも入れない
  「当選発表」「注文期限」しか書かれておらず応募受付期間の記載が無い投稿
  (=既に応募が締め切られた後の当選者向け案内である可能性が高い)では、
  application_start/application_endは両方nullのままにする
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
