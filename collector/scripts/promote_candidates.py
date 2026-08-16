"""product_match_candidates を構造化・昇格するバッチ処理。

confirmed層(needs_manual_review=False)で関連性ありと判定された候補は
products/listings/lotteriesへ反映しstatus='approved'にする。
auto_judgment層(needs_manual_review=True)は抽出結果をraw_data.extractedへ
保存するだけに留め、status='needs_review'として公開しない
(公式リンク・カテゴリ確認は別途行う想定)。
関連性なしと判定された候補はstatus='rejected'にする。

使い方:
    python scripts/promote_candidates.py [件数上限(既定5)]
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import ai_assist, db, dedupe  # noqa: E402
from core.errors import DatabaseError  # noqa: E402
from core.status import compute_lottery_status  # noqa: E402


def _shop_domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    handle = parsed.path.strip("/").split("/")[0]
    return f"{parsed.netloc}/{handle}" if handle else parsed.netloc


def promote_batch(limit: int = 5) -> None:
    if not ai_assist._ai_enabled():  # noqa: SLF001
        print("[error] ANTHROPIC_API_KEY / AI_ASSIST_API_KEY が未設定です")
        return

    candidates = db.list_pending_candidates(limit=limit)
    print(f"対象候補: {len(candidates)}件")

    counts = {"approved": 0, "needs_review": 0, "rejected": 0, "error": 0}

    for candidate in candidates:
        candidate_id = candidate["id"]
        raw_data = candidate["raw_data"]
        text = raw_data.get("notes") or raw_data.get("product_name", "")

        try:
            extracted = ai_assist.extract_lottery_info(text)
            if extracted is None:
                print("[error] AI抽出が無効です(APIキー未設定)")
                return

            if not extracted["is_relevant"]:
                db.update_candidate_status(candidate_id, "rejected")
                counts["rejected"] += 1
                print(f"  [rejected] {raw_data.get('product_name', '')[:40]}")
                continue

            needs_manual_review = raw_data.get("needs_manual_review", False)
            promote_if_category_known = raw_data.get(
                "promote_if_category_known", False
            )
            product_name = extracted.get("product_name") or raw_data.get(
                "product_name", ""
            )
            category = extracted.get("category") or raw_data.get("category")

            # 情報源は公式(一次情報)だが複数カテゴリを横断するアカウントは、
            # AI抽出でカテゴリが判定できた場合に限り保留を解除して昇格させる。
            still_needs_review = needs_manual_review and not (
                promote_if_category_known and extracted.get("category")
            )
            if still_needs_review:
                db.update_candidate_extraction(
                    candidate_id, raw_data, "needs_review", extracted
                )
                counts["needs_review"] += 1
                print(f"  [needs_review] {product_name[:40]}")
                continue

            source_url = raw_data.get("source_url", "")
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
            print(f"  [approved:{match.decision.value}] {product_name[:40]}")

        except DatabaseError as exc:
            counts["error"] += 1
            print(f"  [error] candidate={candidate_id}: {exc}")

    print(
        f"完了: approved={counts['approved']} needs_review={counts['needs_review']} "
        f"rejected={counts['rejected']} error={counts['error']}"
    )


if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    promote_batch(limit=limit_arg)
