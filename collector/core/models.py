"""Collector が扱う共通データモデル。

サイトごとの Collector は、生データをここで定義する CollectedItem に
正規化して core 層へ渡す。DB (products/listings/lotteries) への
振り分けは core.dedupe / core.db が Phase 3 以降で担当する。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class LotteryStatus(str, Enum):
    SOON = "soon"
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SourceMethod(str, Enum):
    """fetch() が実際にどの方式でデータを取得したか。ログ・優先順位検証に使う。"""

    OFFICIAL_API = "official_api"
    RSS = "rss"
    STATIC_HTML = "static_html"
    HTTP_REQUEST = "http_request"
    BROWSER_AUTOMATION = "browser_automation"
    THIRD_PARTY_API = "third_party_api"  # 例: twitterapi.io 等、サイト自身の公式APIではない仲介API
    SITEMAP = "sitemap"  # sitemap.xml 等、クローラー向けに公開されている構造化フィード


class CollectedItem(BaseModel):
    """1つの商品/販売枠について Collector.normalize() が返す正規化済みレコード。

    要求仕様書の必須フィールド（商品名・商品URL・販売店名・画像URL・価格・定価・
    在庫状態・抽選受付開始/終了日時・当選発表日時・発売日時・応募条件・購入条件・
    その他重要な説明・情報取得元URL・最終確認日時）に対応する。
    """

    product_name: str
    product_url: HttpUrl
    shop_name: str
    shop_official_url: HttpUrl | None = None
    image_url: HttpUrl | None = None
    # AI補助抽出に読ませる添付画像(告知画像等)。捏造厳禁のため本文に無い情報を
    # AIが画像から読み取れた場合のみ反映する(候補への保存用。画像自体の保存はしない)。
    image_urls: list[HttpUrl] = Field(default_factory=list)

    price: int | None = None
    retail_price: int | None = None
    stock_status: StockStatus = StockStatus.UNKNOWN

    lottery_title: str | None = None
    lottery_status: LotteryStatus | None = None
    application_start: datetime | None = None
    application_end: datetime | None = None
    result_date: datetime | None = None
    release_date: datetime | None = None

    application_conditions: str | None = None
    purchase_conditions: str | None = None
    notes: str | None = None

    jan: str | None = None
    category: str | None = None

    source_url: HttpUrl
    checked_at: datetime

    # このレコードを生成した fetch 方式（優先順位: API > RSS > 静的HTML > requests > Playwright）
    source_method: SourceMethod = SourceMethod.HTTP_REQUEST

    # True の場合、公式リンク・カテゴリ確認等が済むまで
    # products/listings/lotteries への自動反映を保留する（例: X監視のauto_judgment層）
    needs_manual_review: bool = False

    # True の場合、情報源自体は信頼できる一次情報（公式ショップ等）だが
    # 複数カテゴリを横断するため category が未確定なケース。
    # AI補助抽出で category が判定できた候補に限り、needs_manual_review を
    # 満たしていても自動昇格を許可する（判定できなければ引き続き保留）。
    promote_if_category_known: bool = False

    # True の場合、category は確定しているがアカウント自身は実施店舗ではない
    # 「まとめ/情報アカウント」。アカウント自身の表示名をshopとして使うと
    # 実在しない店舗を捏造することになるため、AI補助抽出で本文中の実施店舗名が
    # 判定できた候補に限り自動昇格を許可する（判定できなければ引き続き保留）。
    shop_name_required: bool = False


class ChangeType(str, Enum):
    NEW_PRODUCT = "new_product"
    SALE_STARTED = "sale_started"
    SALE_ENDED = "sale_ended"
    BACK_IN_STOCK = "back_in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PRICE_DROP = "price_drop"
    PRICE_INCREASE = "price_increase"
    LOTTERY_STARTED = "lottery_started"
    LOTTERY_ENDED = "lottery_ended"
    LOTTERY_INFO_CHANGED = "lottery_info_changed"


class ChangeEvent(BaseModel):
    change_type: ChangeType
    item: CollectedItem
    previous: CollectedItem | None = None
    detail: str | None = None


class ChangeSet(BaseModel):
    """1回の Collector 実行で検出された変化の集合。"""

    events: list[ChangeEvent] = Field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_changes(self) -> bool:
        return len(self.events) > 0


class PersistResult(BaseModel):
    """persist() の結果。新規/更新/スキップ件数と個別エラーを集計する。

    重複排除・差分検知の具体的なやり方（URL単位の既知チェックか、
    フィールド単位の差分比較か）はCollectorの性質に応じて異なるため、
    ここでは結果の型だけを共通化する。
    """

    new_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class RunStats(BaseModel):
    """collector_runs テーブルへ記録する実行サマリ。"""

    collector_key: str
    started_at: datetime
    finished_at: datetime | None = None
    fetched_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    error_count: int = 0
    error_details: list[str] = Field(default_factory=list)
    status: str = "running"
