"""まとめ/情報アカウント自身が実施店舗として誤って公開されているlotteriesを
needs_reviewへ差し戻す一回限りの是正スクリプト。

PokeGetInfoMain等はconfirmed層だった当時、本文から実施店舗名が抽出でき
なかった投稿でもアカウント自身の表示名がshops.nameとして使われ、実在しない
「店舗」として公開されてしまっていた(config/x_accounts.yamlでshop_name_
required: trueを付与しauto_judgment層へ移動済み、今後の新規収集分は
本文から実施店舗名が判明した場合のみ公開される)。

既存の該当lotteriesは、実施店舗が特定できない以上products/lotteriesへ
公開したままにできないため、lotteryを削除し、対応する候補(product_match_
candidates)をneeds_reviewへ差し戻す。

使い方:
    python scripts/fix_aggregator_shop_attribution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import yaml  # noqa: E402

from core import db  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "x_accounts.yaml"


def _shop_name_required_handles() -> list[str]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return [
        a["handle"] for a in config["accounts"] if a.get("shop_name_required", False)
    ]


def main() -> None:
    client = db.get_client()
    handles = _shop_name_required_handles()
    print(f"対象アカウント: {handles}\n")

    reverted = 0
    products_deleted = 0

    for handle in handles:
        domain = f"x.com/{handle}"
        shop_rows = client.table("shops").select("id, name").eq("domain", domain).execute().data
        if not shop_rows:
            continue
        shop_id = shop_rows[0]["id"]

        lotteries = (
            client.table("lotteries")
            .select("id, title, product_id")
            .eq("shop_id", shop_id)
            .execute()
            .data
        )

        for lot in lotteries:
            candidates = (
                client.table("product_match_candidates")
                .select("id, raw_data, status")
                .eq("candidate_product_id", lot["product_id"])
                .eq("status", "approved")
                .execute()
                .data
            )

            client.table("lotteries").delete().eq("id", lot["id"]).execute()
            reverted += 1
            print(f"  [reverted] {lot['title'][:50]} (shop={shop_rows[0]['name']})")

            for c in candidates:
                client.table("product_match_candidates").update(
                    {"status": "needs_review"}
                ).eq("id", c["id"]).execute()

            remaining = (
                client.table("lotteries")
                .select("id", count="exact")
                .eq("product_id", lot["product_id"])
                .limit(1)
                .execute()
            )
            if remaining.count == 0:
                client.table("products").delete().eq("id", lot["product_id"]).execute()
                products_deleted += 1

    print(f"\n完了: reverted={reverted} products_deleted={products_deleted}")


if __name__ == "__main__":
    main()
