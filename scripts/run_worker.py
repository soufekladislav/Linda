from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sqlalchemy import text

from app.db import db
from app.legacy_features import refresh_action_items, schedule_due_publications
from app.workers import cleanup_legacy_chat_jobs, process_available_jobs, recover_stale_jobs, remind_unresolved_failures
from app.proactivity import enqueue_due_planned_tasks


def main() -> int:
    lock_name = "linda:cron_worker"
    with db() as conn:
        acquired = int(conn.execute(text("SELECT GET_LOCK(:name,0)"), {"name": lock_name}).scalar() or 0)
        if acquired != 1:
            print(json.dumps({"ok": True, "busy": True, "message": "Předchozí worker ještě běží."}, ensure_ascii=False))
            return 0
        try:
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('linda_cron_last_run',:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v": datetime.now().isoformat(timespec="seconds")})
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('linda_cron_last_worker','cli-all') ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"))
            prep_errors=[]
            try: cleanup_legacy_chat_jobs()
            except Exception as exc: prep_errors.append({"step":"legacy_chat_cleanup","error":str(exc)[:500]})
            try: recovered = recover_stale_jobs(20)
            except Exception as exc: recovered=0; prep_errors.append({"step":"recover_stale","error":str(exc)[:500]})
            try: proactive = enqueue_due_planned_tasks()
            except Exception as exc: proactive={"count":0,"items":[]}; prep_errors.append({"step":"planned_tasks","error":str(exc)[:500]})
            try: scheduled = schedule_due_publications()
            except Exception as exc: scheduled=[]; prep_errors.append({"step":"schedule_publications","error":str(exc)[:500]})
            try: refresh_action_items()
            except Exception as exc: prep_errors.append({"step":"refresh_actions","error":str(exc)[:500]})
            try: reminders=remind_unresolved_failures(limit=8)
            except Exception as exc: reminders=0; prep_errors.append({"step":"failure_reminders","error":str(exc)[:500]})
            groups = []
            for grp, limit in (("publication", 5), ("text", 5), ("media", 4), ("maintenance", 3)):
                try: group_result=process_available_jobs(limit=limit, worker_group=grp)
                except Exception as exc: group_result={"ok":False,"claimed":0,"done":0,"failed":0,"details":[],"worker_error":str(exc)[:1000]}
                groups.append({"group": grp, **group_result})
            claimed = sum(int(x.get("claimed") or 0) for x in groups)
            done = sum(int(x.get("done") or 0) for x in groups)
            failed = sum(int(x.get("failed") or 0) for x in groups)
            # A business/content job may fail while the worker itself is perfectly
            # healthy. Keep those concepts separate so one invalid AI output does
            # not make diagnostics claim that Linda/cron is down.
            result = {
                "ok": True,
                "worker_ok": True,
                "completed_with_errors": failed > 0,
                "recovered_stale": recovered,
                "planned_tasks_enqueued": proactive,
                "scheduled_publications": scheduled,
                "groups": groups,
                "prep_errors": prep_errors,
                "failure_reminders": reminders,
                "claimed": claimed,
                "done": done,
                "failed": failed,
            }
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('linda_cron_last_result',:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v": json.dumps(result, ensure_ascii=False, default=str)[:10000]})
            print(json.dumps(result, ensure_ascii=False, default=str))
            return 0
        finally:
            try:
                conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
