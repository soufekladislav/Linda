# Upgrade 0.4.2 → 0.4.3

## Preserve
- `.env`
- `storage/uploads`
- live MariaDB data

## Install

```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz
pip install -r requirements.txt
python scripts/check_install.py
mkdir -p tmp
touch tmp/restart.txt
curl --max-time 20 -i https://socialmedia.freshcrackers.cz/health
```

`check_install.py` now also verifies `MEDIA_SIGNING_KEY` and Pillow.

## Chat smoke test
1. Open a project → Linda.
2. Send `Ahoj Lindo, co o tomto projektu víš?`
3. The answer should appear without waiting for cron or opening Fronta.
4. In the developer manifest inspect `ai_engine.audit.recent_ai_events`.
5. A successful turn contains `run_id` and task type `linda_chat`, or `fallback_used: true` if the current Engine accepted only the compatibility route.

## Existing media optimization
Start with a dry run:

```bash
python scripts/optimize_media.py --limit 20
```

Apply a small batch:

```bash
python scripts/optimize_media.py --apply --limit 20
```

Only after checking the app, optionally remove old source files when they are no longer referenced:

```bash
python scripts/optimize_media.py --apply --delete-source --limit 20
```

The upgrade itself never automatically deletes or rewrites existing media.

## Storage cleanup (optional, always dry-run first)

```bash
python scripts/media_cleanup.py --older-than 30
```

After reviewing the list:

```bash
python scripts/media_cleanup.py --apply --older-than 30
```

Thumbnail cache can be safely recreated and can be included explicitly with `--include-thumbs`.
