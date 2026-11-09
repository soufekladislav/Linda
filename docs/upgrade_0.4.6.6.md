# Upgrade Linda 0.4.6.5 → 0.4.6.6

Production hotfix for the queue / WordPress publishing boundary while Fresh AI Engine structured-output handling is being repaired separately.

## What changes

- Linda no longer treats HTTP 200 from AI Engine as success when the Engine run itself is `failed` / `error` or a requested JSON contract is explicitly unvalidated.
- Structured-output failures are shown to the user in Czech and are not retried three times automatically. The original data stays untouched; use **Zkusit znovu** after Engine is fixed.
- A scheduled WordPress article with obviously short legacy copy can still ask AI Engine for expansion, but if expansion fails Linda **does not jam the publication queue**. It places the article on `content_short` hold and asks for review/retry.
- Scheduled publication creation first checks for an already active publication job. If the same publication already failed, Linda puts the post on a repair hold instead of silently creating another copy of the job.
- Admin **Zrušit** on a publication job now also places the post on a manual hold, preventing the scheduler from silently recreating it two minutes later.
- Worker health is separated from business-job failures. A failed content job no longer makes diagnostics claim that the cron/worker itself is down.
- Publication progress is more explicit (`kontrola → AI enrichment when needed → Fresh Core → save result`).
- Content and Calendar show a clearer hold label (`dopracovat text`, `vyžaduje opravu`).

## Database / requirements

No database migration. No requirements change.

Preserve:

- `.env`
- `storage/uploads/`
- the existing MariaDB database

## Recommended deploy

```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz

python -m py_compile app/*.py scripts/*.py
mkdir -p tmp
touch tmp/restart.txt
curl --max-time 20 -sS https://socialmedia.freshcrackers.cz/health
python scripts/run_worker.py
```

Expected health version after restart: `0.4.6.6`.

If an old publication is already in `failed`, open **Fronta úloh → Zkusit znovu** after the AI Engine structured-output fix is deployed. New structured-output failures stop after one attempt instead of filling the queue.
