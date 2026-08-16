"""既存の needs_review 候補のうち、official_multi_category アカウントで
既にAI抽出済み(raw_data.extracted)かつ category が判定できているものを
products/lotteries へ昇格させる一回限りの再処理スクリプト。

promote_if_category_known フラグ導入前に取り込まれた候補は raw_data に
このフラグを持たないため、config/x_accounts.yaml から official_multi_category
アカウント一覧を読み直して判定する。AIへの再問い合わせは行わず、
既存の extracted をそのまま使う(APIコスト節約)。

使い方:
    python scripts/reprocess_needs_review.py [件数上限(既定200)]
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import yaml  # noqa: E402

from core import db, dedupe  # noqa: E402
from core.errors import DatabaseError  # noqa: E402
from core.status import compute_lottery_status  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "x_accounts.yaml"


def _official_multi_category_handles() -> set[str]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return {
        a["handle"]
        for a in config["accounts"]
        if a.get("official_multi_category", False)
    }


def _shop_domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    handle = parsed.path.strip("/").split("/")[0]
    return f"{parsed.netloc}/{handle}" if handle else parsed.netloc


def main(limit: int = 200) -> None:
    official_handles = _official_multi_category_handles()
    print(f"official_multi_category対象: {sorted(official_handles)}\n")

    candidates = db.list_candidates_by_status("needs_review", limit=limit)
    print(f"needs_review 件数: {len(candidates)}件\n")

    counts = {"approved": 0, "skipped": 0, "error": 0}

    for candidate in candidates:
        candidate_id = candidate["id"]
        raw_data = candidate["raw_data"]
        shop_name = raw_data.get("shop_name", "")
        handle = shop_name.replace("X: @", "") if shop_name.startswith("X: @") else ""

        if handle not in official_handles:
            counts["skipped"] += 1
            continue

        extracted = raw_data.get("extracted")
        if not extracted or not extracted.get("is_relevant") or not extracted.get(
            "category"
        ):
            counts["skipped"] += 1
            continue

        product_name = extracted.get("product_name") or raw_data.get(
            "product_name", ""
        )
        category = extracted.get("category")
        source_url = raw_data.get("source_url", "")

        try:
            shop_domain = _shop_domain_from_url(source_url)
            shop_id = db.upsert_shop(
                name=raw_data.get("shop_name", shop_domain), domain=shop_domain
            )
            match = dedupe.match_product(product_name, category)
            db.insert_lottery(
                product_id=match.product_id,
                shop_id=shop_id,
                title=product_name,
                sale_type=extracted.get("sale_type"),
                url=source_url,
                application_start=extracted.get("application_start"),
                application_end=extracted.get("application_end"),
                result_date=extracted.get("result_date"),
                release_date=extracted.get("release_date"),
                conditions=extracted.get("conditions"),
                status=compute_lottery_status(
                    extracted.get("application_start"), extracted.get("application_end")
                ),
                source_url=source_url,
            )
            db.update_candidate_status(
                candidate_id, "approved", candidate_product_id=match.product_id
            )
            counts["approved"] += 1
            print(f"  [approved:{match.decision.value}] @{handle} {product_name[:40]}")
        except DatabaseError as exc:
            counts["error"] += 1
            print(f"  [error] candidate={candidate_id}: {exc}")

    print(
        f"\n完了: approved={counts['approved']} skipped={counts['skipped']} "
        f"error={counts['error']}"
    )


if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(limit=limit_arg)
