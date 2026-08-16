"""lotteries.status を現在時刻と応募開始/終了日時から再計算する。

application_start/application_end が判明している行は、時間の経過だけで
soon -> open -> closed と状態が変わりうる。ツイートの再取得がなくても
定期的にこのスクリプトを実行し、statusを実態に合わせて更新する
(「変更があった場合だけ更新」に倣い、実際に値が変わる行だけ書き込む)。

使い方:
    python scripts/refresh_lottery_status.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import db  # noqa: E402
from core.status import compute_lottery_status  # noqa: E402


def main() -> None:
    rows = db.list_lotteries_for_status_refresh()
    updated = 0
    unchanged = 0

    for row in rows:
        new_status = compute_lottery_status(
            row["application_start"], row["application_end"]
        )
        if new_status != row["status"]:
            db.update_lottery_status(row["id"], new_status)
            updated += 1
            print(f"  [{row['status']} -> {new_status}] id={row['id']}")
        else:
            unchanged += 1

    print(f"完了: updated={updated} unchanged={unchanged} total={len(rows)}")


if __name__ == "__main__":
    main()
