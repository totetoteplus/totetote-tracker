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


PRODUCT_IMAGE_INDEX_DESCRIPTION = (
    "渡した画像のうち、商品そのものが画面の主役として写っている、"
    "商品カタログ写真のようにクリーンな画像があれば、そのインデックス"
    "(0始まり、渡した順)を返す。以下はすべて対象外とし、該当する画像が"
    "無ければnull:\n"
    "  - 文字だけの告知バナー・お知らせ見出し・ロゴのみの画像\n"
    "  - 応募期間・当選発表・価格・購入条件などの説明文/日程表が"
    "画像の大部分を占めるチラシ/ポスター型の告知画像"
    "(商品写真が小さく挿入されているだけのものを含む)\n"
    "  - 複数の異なる商品を並べた比較表・一覧画像\n"
    "  - 色付きの見出しバナー(店舗名+「限定抽選会」等の帯)や日程表が"
    "画像内にひとつでも存在する画像。この種の画像は商品写真が"
    "画像の半分以上を占めていても対象外とする(商品写真部分だけを"
    "切り出すことはできないため)\n"
    "選んでよいのは、商品(パッケージ/BOX/フィギュア等)の写真のみで"
    "画面のほぼ全体が構成されており、文字情報が商品名・ロゴ程度に"
    "限られる画像だけ"
)


def _build_extraction_schema(num_images: int) -> dict[str, Any]:
    """image_urls の枚数に応じて product_image_index の候補indexを絞ったスキーマを作る。"""
    schema = json.loads(json.dumps(_EXTRACTION_SCHEMA_BASE))
    if num_images > 0:
        schema["properties"]["product_image_index"] = {
            "anyOf": [
                {"type": "integer", "enum": list(range(num_images))},
                {"type": "null"},
            ],
            "description": PRODUCT_IMAGE_INDEX_DESCRIPTION,
        }
        schema["required"].append("product_image_index")
    return schema


_EXTRACTION_SCHEMA_BASE = {
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
        "price": {
            "type": ["integer", "null"],
            "description": (
                "商品そのものの販売価格(税込)。「¥◯◯」という金額がテキストや"
                "画像にあっても、直前・直後に「レシート」「購入証明」「以上の"
                "購入」「応募資格」「対象」等の語が伴う場合、それは応募資格の"
                "金額しきい値であり商品価格ではない。例:"
                "「¥300以上の購入が確認できるレシートが必要です」→price は"
                "商品価格ではなく必ずnull(この300という数字をpriceに入れる"
                "ことは絶対に禁止)。送料・予約金・手数料も同様にnull。"
                "商品自体の値札・価格として明記された金額のみpriceに入れる"
            ),
        },
        "application_start": {
            "type": ["string", "null"],
            "description": (
                "応募(抽選申込)の受付が始まる日時。ISO8601形式 "
                "(例: 2026-08-17T11:00:00+09:00)。時刻不明なら日付のみ。"
                "「当選発表」「注文期限」「購入期限」等、応募受付とは別のイベントの"
                "日時をここに入れない。「◯月◯日 ◯時◯分まで」のように締切"
                "(終える方の日時)しか書かれておらず、開始日時が本文中に別途"
                "明記されていない場合、その日時は application_end であって"
                "application_start ではない(絶対に取り違えない)。開始日時が"
                "不明ならapplication_startはnullのままにする"
            ),
        },
        "application_end": {
            "type": ["string", "null"],
            "description": (
                "応募(抽選申込)の受付が終わる日時。「当選発表日時」(result_date)や"
                "「当選者向けの注文期限・購入期限」とは別物であり、混同しないこと。"
                "本文に応募受付終了の日時が書かれていなければnull"
                "(注文期限しか書かれていない投稿は多くの場合、応募自体は既に締め切られた"
                "後の当選者向け案内である)。"
                "「◯月◯日 ◯時◯分まで」「受付期間: 〜◯月◯日」のように締切のみが"
                "明記されている投稿では、その日時は必ずここ(application_end)に"
                "入れる(application_startに誤って入れない)"
            ),
        },
        "result_date": {
            "type": ["string", "null"],
            "description": "当選発表・抽選結果発表の日時",
        },
        "release_date": {
            "type": ["string", "null"],
            "description": (
                "商品の発売日。ISO8601形式で、年月日まで判明している場合のみ返す"
                "(例: 2026-12-01)。年月のみなど日が不明な場合はnullにする"
                "(不完全な日付を返さない)"
            ),
        },
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

添付画像がある場合、告知内容をまとめた画像(応募期間・当選発表日・価格等が
記載されたカード状の告知画像)であることが多い。本文と同様に画像内の文字も
読み取り、記載されている情報を抽出してよい。ただし不鮮明・判読不能な部分を
推測で埋めることは禁止する。

最重要ルール（絶対に守ること）:
- テキスト(および添付画像がある場合はその内容)に明記されていない情報は絶対に推測・補完しない。不明な項目は必ず null にする
- 価格・日付・条件などを「だいたいこれくらいだろう」で埋めない
- 抽選販売・先着販売・受注販売の告知として明確に関連性がない投稿（無関係な話題、他の抽選のRT、コラボ告知だが販売方式が書かれていない等）は is_relevant を false にする
- pokemon/yugioh/onepiece/dragonball(トレーディングカードゲーム)カテゴリにおいて、対象商品が
  カード本体(拡張パック/BOX/スターター・ストラクチャーデッキ/シングルカード等)ではなく、
  プロテクター・スリーブ・デッキケース・プレイマット・カードファイル・バインダー・収納ポーチ等の
  「カードゲーム関連グッズ(付属品)」のみである場合は is_relevant を false にする
  (ichibankuji/chiikawa/watch/nike/beyblade等、商品自体がグッズであるカテゴリには適用しない)
- 日付に年が明記されていない場合、文脈上の基準日（渡された「本日の日付」）と同じ年と判断してよいが、月日だけで年をまたぐ可能性が疑われる場合は null にする
- sale_type は「抽選」なら lottery、「先着」「くじ」なら firstcome、「受注」「予約」なら backorder。判断できなければ null
- price は商品そのものの販売価格のみ。応募条件として提示される「¥300以上の
  購入が確認できるレシートが必要」のようなレシート/購入証明の金額しきい値、
  送料、予約金、手数料等を商品価格と誤認しない(商品自体の価格が別途
  明記されていない場合はnull)
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
  「受付期間 8月26日 23:59まで」のように締切日時しか書かれておらず開始日時が
  別途明記されていない投稿では、その日時はapplication_end(受付終了)であり、
  application_start(受付開始)ではない。取り違えるとステータス判定
  (open/closed)が逆転するため特に注意する
- product_image_index(渡された場合)は、画面のほぼ全体が商品(パッケージ/BOX/
  フィギュア等)の写真だけで構成されているクリーンな画像がある場合のみ、
  そのインデックスを返す。色付きの見出しバナーや応募期間・価格等の説明文/
  日程表が画像内に少しでも含まれる「チラシ/ポスター型」の画像は、商品写真が
  大きく写っていても対象外にする(商品写真部分だけを切り出すことはできない
  ため)
"""


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AI_ASSIST_API_KEY")


def _ai_enabled() -> bool:
    return bool(_api_key())


# Xの添付画像は最大4枚まで付くが、実際に有用な情報が追加で得られるのは
# ほぼ1〜2枚目までのため、コスト抑制のため2枚までに制限する。
MAX_IMAGES_PER_REQUEST = 2

DATE_LIKE_FIELDS = ("application_start", "application_end", "result_date")
ALL_DATE_FIELDS = DATE_LIKE_FIELDS + ("release_date",)


def _sanitize_date_fields(result: dict[str, Any]) -> dict[str, Any]:
    """日付系フィールドがISO8601として不正な場合はnullに落とす。

    「2026-12」のような年月のみの値等、DBのtimestamptz列に入らない不完全な
    値をモデルが返すことがあるため、書き込み前に必ず検証する(未検証のまま
    渡すとDB書き込みが例外で落ち、そのバッチの残り全件が処理されなくなる)。
    """
    for field in ALL_DATE_FIELDS:
        value = result.get(field)
        if not value:
            continue
        try:
            datetime.fromisoformat(value)
        except ValueError:
            result[field] = None
    return result


def _call_extraction(
    text: str, ref_date: datetime, image_urls: list[str]
) -> dict[str, Any] | None:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"本日の日付: {ref_date.strftime('%Y-%m-%d')}\n\n"
                f"テキスト:\n{text}"
            ),
        }
    ]
    for url in image_urls:
        content.append({"type": "image", "source": {"type": "url", "url": url}})

    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=1024,
        # 構造化抽出タスクのため出力のブレを抑える(temperature未指定だと
        # 同じ入力でも呼び出しごとに結果が変わることがあった)。
        temperature=0,
        # システムプロンプトは全呼び出しで共通のため、プロンプトキャッシュで
        # 入力トークン代を抑える(2回目以降のヒットで当該分がほぼ無料になる)。
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": _build_extraction_schema(len(image_urls)),
            }
        },
        messages=[{"role": "user", "content": content}],
    )

    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return None

    result: dict[str, Any] = json.loads(text_block.text)

    image_index = result.pop("product_image_index", None)
    result["product_image_url"] = (
        image_urls[image_index]
        if image_index is not None and 0 <= image_index < len(image_urls)
        else None
    )

    return _sanitize_date_fields(result)


_IMAGE_ONLY_SCHEMA = {
    "type": "object",
    "properties": {
        "product_image_index": {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
            "description": PRODUCT_IMAGE_INDEX_DESCRIPTION,
        },
    },
    "required": ["product_image_index"],
    "additionalProperties": False,
}


def extract_product_image(text: str, image_urls: list[str]) -> str | None:
    """添付画像から商品写真として使えるものだけを軽量に判定する(商品画像ストック用)。

    products.image_url が既に設定されている商品には呼ばない想定
    (同じ商品なら既存画像を使い回し、未取得の商品でだけ新たに取得する)。
    extract_lottery_info本体のフル抽出より小さいスキーマ・トークン数で
    呼び出せるため、商品画像だけが目的の場合はこちらの方が安価。
    """
    if not _ai_enabled() or not image_urls:
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())

    used_images = image_urls[:MAX_IMAGES_PER_REQUEST]
    content: list[dict[str, Any]] = [{"type": "text", "text": f"テキスト:\n{text}"}]
    for url in used_images:
        content.append({"type": "image", "source": {"type": "url", "url": url}})

    schema = json.loads(json.dumps(_IMAGE_ONLY_SCHEMA))
    schema["properties"]["product_image_index"]["anyOf"][0]["enum"] = list(
        range(len(used_images))
    )

    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=64,
        temperature=0,
        system=[
            {
                "type": "text",
                "text": (
                    "あなたはXの投稿から商品写真を選ぶアシスタントです。"
                    "推測で埋めず、該当が無ければnullを返してください。"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )

    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return None

    result = json.loads(text_block.text)
    index = result.get("product_image_index")
    return used_images[index] if index is not None and 0 <= index < len(used_images) else None


_PRODUCT_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matched_index": {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
            "description": (
                "new_nameが、candidatesのいずれかとまったく同じ商品(同じ構成物・"
                "同じ型番/レアリティ)を指している場合のみ、そのインデックス"
                "(0始まり)を返す。表記ゆれ(括弧の種類・区切り記号・語順・"
                "店舗ごとの言い回し)は同一商品とみなしてよいが、MEGA版か通常版か、"
                "単品かセットか、含まれるアイテムの構成が違う場合は別商品として"
                "扱う。同一と断定できない場合は必ずnull(推測で統合しない)"
            ),
        },
    },
    "required": ["matched_index"],
    "additionalProperties": False,
}


def match_product_name(new_name: str, candidates: list[str]) -> int | None:
    """新規商品名候補が既存商品名のいずれかと同一商品かをAIに判定させる。

    core.dedupe.match_product() の完全一致dedupeで一致しなかった場合の
    第二段判定。同一シリーズの商品が投稿ごとの表記ゆれ(括弧の種類・区切り
    記号・語順違い等)で商品が何行にも分裂してしまう問題(例: 「30th
    CELEBRATION」関連投稿が34行に分裂していた)に対応するため追加した。
    確信が持てない場合は必ずNoneを返す前提のプロンプトにしてあるため、
    この関数がNoneを返した場合は新規商品として扱ってよい(誤統合より
    表記ゆれ重複の方が実害が小さいため、迷ったら統合しない側に倒す)。
    """
    if not _ai_enabled() or not candidates:
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())

    candidate_list = "\n".join(f"{i}: {c}" for i, c in enumerate(candidates))
    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=32,
        temperature=0,
        system=[
            {
                "type": "text",
                "text": (
                    "あなたは商品カタログの名寄せ判定アシスタントです。"
                    "推測で統合せず、同一商品と断定できない場合はnullを返して"
                    "ください。"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": _PRODUCT_MATCH_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": f"新しい商品名:\n{new_name}\n\n既存候補:\n{candidate_list}",
            }
        ],
    )

    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return None

    result = json.loads(text_block.text)
    index = result.get("matched_index")
    return index if index is not None and 0 <= index < len(candidates) else None


def extract_lottery_info(
    text: str,
    reference_date: datetime | None = None,
    image_urls: list[str] | None = None,
) -> dict[str, Any] | None:
    """自由記述のテキスト(+添付画像)から抽選/先着/受注販売の構造化情報を抽出する。

    コスト抑制のため2段階で処理する:
      1. まずテキストのみで抽出する(画像なしなので安価)。
      2. 関連性ありと判定され、かつ応募期間/当選発表日が本文からは一切
         判明しなかった場合に限り、画像も添えて再抽出する(応募期間等が
         画像側にしか書かれていないケースの取りこぼしを防ぐ)。
    テキストだけで十分な情報が取れた投稿(=大半)は1回の安価な呼び出しで
    完結し、画像込みの2回目呼び出しは本当に必要な場合だけ発生する。

    AI_ASSIST_API_KEY / ANTHROPIC_API_KEY が未設定の場合は None を返す
    (呼び出し側でルールベースのフォールバックを行うか、処理をスキップする)。
    """
    if not _ai_enabled():
        return None

    ref_date = reference_date or datetime.now(timezone.utc)

    text_only_result = _call_extraction(text, ref_date, image_urls=[])
    if text_only_result is None:
        return None

    used_images = (image_urls or [])[:MAX_IMAGES_PER_REQUEST]
    needs_image_pass = (
        used_images
        and text_only_result.get("is_relevant")
        and not any(text_only_result.get(f) for f in DATE_LIKE_FIELDS)
    )
    if not needs_image_pass:
        return text_only_result

    image_result = _call_extraction(text, ref_date, image_urls=used_images)
    return image_result if image_result is not None else text_only_result
