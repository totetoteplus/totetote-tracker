"""needs_review 候補をアカウント単位で集計・一覧表示する（読み取り専用）。

auto_judgment層のうち、実際には信頼できる情報源(tier格上げ候補)がないか
判断材料を得るためのレビュー用スクリプト。DBへの書き込みは行わない。

使い方:
    python scripts/review_needs_review.py [件数上限(既定200)]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import db  # noqa: E402


def main(limit: int = 200) -> None:
    candidates = db.list_candidates_by_status("needs_review", limit=limit)
    print(f"needs_review 件数: {len(candidates)}件\n")

    by_shop: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        raw = c["raw_data"]
        shop_name = raw.get("shop_name", "?")
        by_shop[shop_name].append(raw)

    for shop_name, rows in sorted(by_shop.items(), key=lambda kv: -len(kv[1])):
        categories = defaultdict(int)
        for r in rows:
            extracted = (r.get("extracted") or {})
            cat = extracted.get("category") or "?"
            categories[cat] += 1
        cat_summary = ", ".join(f"{k}:{v}" for k, v in categories.items())
        print(f"[{shop_name}] {len(rows)}件  category内訳: {cat_summary}")
        for r in rows[:3]:
            extracted = (r.get("extracted") or {})
            name = extracted.get("product_name") or r.get("product_name", "")
            print(f"    - {name[:60]}")
        print()


if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(limit=limit_arg)
