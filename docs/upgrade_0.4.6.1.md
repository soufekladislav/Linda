# Linda 0.4.6.1 – production hotfix

- Calendar drag & drop uses dedicated JSON endpoint and persisted reload.
- Calendar Detail opens the shared post editor instead of redirecting to Content.
- Shared post editor supports text/status/schedule/channel, current media preview, AI image generation, upload and media-library assignment.
- Queue reconciles abandoned running jobs and closes obsolete queued `linda_chat` jobs (chat is synchronous).
- Queue page polls live status and shows worker heartbeat.
- Cron worker stores last-run/last-result diagnostics in `app_meta`.
- Developer manifest reports stale running jobs, oldest queue items and cron heartbeat.

No destructive database migration is performed. `.env` and `storage/uploads` must be preserved.

## Recommended production cron

Run one CLI worker every 2 minutes:

```cron
*/2 * * * * cd /home2/lauhannq/socialmedia.freshcrackers.cz && /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/python scripts/run_worker.py >> storage/worker.log 2>&1
```

The worker uses the same MariaDB lock as the HTTP cron endpoint, so runs do not overlap.
