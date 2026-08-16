"""前回取得データとの差分検知。Phase 4で実装する。

source_pages.content_hash によるページ単位の早期スキップ（内容が全く
変わっていなければ以降の比較処理自体を省略する）と、CollectedItem単位の
フィールド比較（新商品／販売開始・終了／在庫復活・切れ／値下げ・値上げ／
抽選開始・終了／抽選情報変更）の2段構えで実装する想定。

変化がない場合はDB更新を行わないため、呼び出し側（collectors/*.py の
BaseCollector.run()）は ChangeSet が空なら upsert をスキップしてよい。
"""

from __future__ import annotations

import hashlib

from core.models import ChangeSet, CollectedItem


def content_hash(raw_content: str) -> str:
    """source_pages.content_hash に保存する取得元ページのハッシュ値。"""

    return hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def compute_changes(
    previous: list[CollectedItem], current: list[CollectedItem]
) -> ChangeSet:
    """前回・今回の CollectedItem 一覧を比較し ChangeSet を返す（Phase 4で実装）。"""

    raise NotImplementedError("core.diff.compute_changes は Phase 4 で実装する")
