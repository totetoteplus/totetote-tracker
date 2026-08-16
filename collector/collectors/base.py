"""すべてのサイト別Collectorが実装する共通インターフェース。

サイトごとに独立したモジュール（collectors/takaratomy.py 等）を作り、
このBaseCollectorを継承する。fetch/parse は必ずサイト固有実装が必要。
normalize は多くの場合共通化できるが、必要ならサイト側で上書きしてよい。

fetch() の実装方式は取得優先順位に従うこと:
  公式API > RSS > 静的HTML取得 > requests(HTTP) > Playwright(ブラウザ自動操作)
実際に使った方式は CollectedItem.source_method に記録する。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from core.errors import CollectorError
from core.logging_config import get_logger
from core.models import ChangeSet, CollectedItem, PersistResult, RunStats


class BaseCollector(ABC):
    #: scheduler/config.yaml や source_pages / collector_runs で使う一意キー
    source_key: str
    #: config.yaml の頻度tierと突き合わせる表示名（ログ用途）
    display_name: str = ""

    def __init__(self) -> None:
        if not getattr(self, "source_key", None):
            raise ValueError(f"{type(self).__name__} は source_key を定義する必要がある")
        self.logger: logging.Logger = get_logger(self.source_key)

    # ------------------------------------------------------------------
    # サイトごとに必須実装
    # ------------------------------------------------------------------
    @abstractmethod
    def fetch(self) -> Any:
        """データ取得元から生データを取得する（API応答・RSS XML・HTML文字列等）。

        ネットワークエラー・タイムアウト・CAPTCHA検知等は core.errors の
        該当する例外を送出すること。run() 側でまとめて捕捉・ログ化する。
        """

    @abstractmethod
    def parse(self, raw: Any) -> list[dict]:
        """生データを緩い構造（dictのリスト）へ変換する。

        想定していた構造が崩れている場合は
        core.errors.PageStructureChangedError を送出する。
        """

    # ------------------------------------------------------------------
    # デフォルト実装（必要に応じてサイト側で上書き可）
    # ------------------------------------------------------------------
    def normalize(self, parsed: list[dict]) -> list[CollectedItem]:
        """parse() の出力を CollectedItem へ変換する。

        フィールド名がそのまま CollectedItem と一致する場合はこの既定実装で
        足りるが、サイト固有の変換が必要な場合はオーバーライドする。
        """
        return [CollectedItem.model_validate(row) for row in parsed]

    def detect_changes(
        self, previous: list[CollectedItem], current: list[CollectedItem]
    ) -> ChangeSet:
        """フィールド単位の差分比較が必要なCollector向けの補助メソッド。

        価格・在庫・抽選ステータスなど構造化済みフィールドを持つCollector
        （静的HTML/API系）は persist() の中でこれを呼んで使う想定。
        SNS投稿のようにURL単位の既知チェックで足りるCollectorは使わなくてよい
        （必須のパイプライン段階ではない）。
        """
        from core.diff import compute_changes

        return compute_changes(previous, current)

    # ------------------------------------------------------------------
    # サイトごとに必須実装
    # ------------------------------------------------------------------
    @abstractmethod
    def persist(self, current: list[CollectedItem]) -> PersistResult:
        """正規化済みアイテムをSupabaseへ反映する。

        重複排除・差分検知の具体的な方法（URL単位の既知チェックか、
        products/listingsのフィールド差分比較か）はCollectorが扱うデータの
        性質によって異なるため、ここで各Collectorが判断する。
        変化がない/既知のアイテムはスキップし、不要なDB更新を避けること。
        """

    # ------------------------------------------------------------------
    # 実行エントリポイント（scheduler/run_due.py から呼ばれる想定）
    # ------------------------------------------------------------------
    def run(self) -> RunStats:
        stats = RunStats(
            collector_key=self.source_key,
            started_at=datetime.now(timezone.utc),
        )
        try:
            raw = self.fetch()
            parsed = self.parse(raw)
            stats.fetched_count = len(parsed)

            current = self.normalize(parsed)
            result = self.persist(current)

            stats.new_count = result.new_count
            stats.updated_count = result.updated_count
            stats.error_count += len(result.errors)
            stats.error_details.extend(result.errors)
            stats.status = "success" if not result.errors else "partial_failure"
        except CollectorError as exc:
            stats.error_count += 1
            stats.error_details.append(str(exc))
            stats.status = "failed"
            self.logger.error("collector failed: %s", exc)
        finally:
            stats.finished_at = datetime.now(timezone.utc)

        self.logger.info(
            "run finished: fetched=%d new=%d updated=%d errors=%d status=%s",
            stats.fetched_count,
            stats.new_count,
            stats.updated_count,
            stats.error_count,
            stats.status,
        )

        try:
            from core.db import record_run

            record_run(stats)
        except Exception as exc:  # noqa: BLE001 - ログ記録自体の失敗でrunを失敗扱いにしない
            self.logger.warning("failed to record run stats to DB: %s", exc)

        return stats
