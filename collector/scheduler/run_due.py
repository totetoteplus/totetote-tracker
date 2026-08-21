"""期限が来たCollectorだけ実行するエントリポイント（Phase 5）。

常駐デーモンにはせず、外部cron（GitHub Actions等）から定期的に起動される
短命プロセスとして動かす想定。実行すべきかどうかは
scheduler/config.yaml のスケジュール設定と、DB(collector_runs)に記録された
直近実行時刻から判定する。1つのCollectorの失敗が他のCollectorの実行を
妨げないよう、Collectorごとに例外を捕捉する。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# collector/ 直下を sys.path に追加する
# (scheduler/run_due.py を直接実行した場合、既定では scheduler/ しか path に載らないため)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.base import BaseCollector  # noqa: E402
from collectors.x_monitor import XMonitorCollector  # noqa: E402
from core import db  # noqa: E402
from core.errors import CollectorError  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

# 新しいCollectorを追加したら、ここにも登録すること。
COLLECTOR_REGISTRY: dict[str, type[BaseCollector]] = {
    "x_monitor": XMonitorCollector,
}


def _load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _is_due_fixed_times(
    schedule: dict, last_run: datetime | None, now_utc: datetime
) -> bool:
    """予定時刻を過ぎていて、かつその時刻以降にまだ実行していなければ due とする。

    GitHub Actionsのscheduled workflowは高負荷時に数十分〜1時間以上遅延することが
    実測で確認されているため、上限を設けた「許容ウィンドウ」方式は使わない
    (過去に15分の狭いウィンドウを設けていたところ、実際の起動が毎回ウィンドウを
    過ぎてしまい、Collectorが1週間近く一度も実行されない不具合が発生した)。
    「予定時刻以降にlast_runがない」ことだけを条件にすれば、遅延の大小に関わらず
    実際にワークフローが起動したタイミングで正しく1回だけ実行される。
    """
    tz = ZoneInfo(schedule.get("timezone", "UTC"))
    now_local = now_utc.astimezone(tz)

    for time_str in schedule["times"]:
        hour, minute = (int(x) for x in time_str.split(":"))
        scheduled_today = now_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

        if now_local < scheduled_today:
            continue

        if last_run is None or last_run.astimezone(tz) < scheduled_today:
            return True
    return False


def _is_due_tier(
    tier_name: str, tiers: dict, last_run: datetime | None, now_utc: datetime
) -> bool:
    interval_minutes = tiers[tier_name]["interval_minutes"]
    if last_run is None:
        return True
    return now_utc - last_run >= timedelta(minutes=interval_minutes)


def main() -> int:
    config = _load_config()
    tiers = config.get("tiers", {})
    now_utc = datetime.now(timezone.utc)

    exit_code = 0

    for entry in config.get("collectors", []):
        source_key = entry["source_key"]

        if not entry.get("enabled", False):
            print(f"[skip] {source_key}: disabled")
            continue

        collector_cls = COLLECTOR_REGISTRY.get(source_key)
        if collector_cls is None:
            print(f"[skip] {source_key}: COLLECTOR_REGISTRY未登録")
            continue

        try:
            last_run = db.get_last_run_started_at(source_key)

            schedule = entry.get("schedule")
            if schedule and schedule.get("type") == "fixed_times":
                due = _is_due_fixed_times(schedule, last_run, now_utc)
            elif "tier" in entry:
                due = _is_due_tier(entry["tier"], tiers, last_run, now_utc)
            else:
                print(f"[skip] {source_key}: schedule/tier が未設定")
                continue

            if not due:
                print(f"[skip] {source_key}: 未到来 (last_run={last_run})")
                continue

            print(f"[run] {source_key}")
            collector = collector_cls()
            stats = collector.run()
            print(
                f"[done] {source_key}: status={stats.status} "
                f"fetched={stats.fetched_count} new={stats.new_count} "
                f"errors={stats.error_count}"
            )
            if stats.status == "failed":
                exit_code = 1
        except CollectorError as exc:
            print(f"[error] {source_key}: {exc}")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
