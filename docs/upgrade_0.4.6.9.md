# Upgrade Linda 0.4.6.8 → 0.4.6.9

## Purpose

This is the production P0/P1 stabilization update for Linda. It does **not** require a destructive database migration and must preserve the current `.env`, database, projects, users, content and `storage/uploads` files.

## What changes

### Image pipeline
- Linda calls Fresh AI Engine with `task_type=image_generation`, `operation=image.generate`.
- A queue job succeeds only after Linda receives a real image, validates MIME + decode + dimensions, optimizes it, writes it atomically, binds the media row and creates a thumbnail.
- Text returned instead of an image is a hard image-generation failure, not a successful job.
- Generated images are JPEG by default; PNG is retained only when pixels contain real transparency.
- Default max dimension is 1600 px, JPEG quality 84, thumbnail dimension 720 px.

### Media maintenance
- Older images are optimized in bounded batches.
- Replacement files are verified before DB paths are changed.
- Old files are removed only after the new file is committed and no DB row references the old path.
- Admin diagnostics records baseline/current storage, cumulative bytes saved, optimized file count, remaining large-image candidates and last run.

### Queue / UX
- One failed job cannot stop unrelated claimed jobs in the same worker batch.
- Retry/cancel and related-content deep links are available.
- Queued jobs with previous attempts are shown as `Opakování`.
- User-facing errors are Czech and human-readable; technical details remain available to superadmin diagnostics.
- Content/calendar cards show active work and refresh when the related background job changes state.
- Chat is more compact on desktop and remains full-width/mobile-safe with a sticky composer.
- Completion/failure notifications can deep-link to related content.

## Deploy

Activate the existing venv and go to the app root:

```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz
```

Before extracting the update, keep the current production data untouched:

```bash
cp .env .env.before-0.4.6.9
```

Extract `linda-0.4.6.9-update.zip` over the current app. The update ZIP does not contain `.env` or `storage/uploads`.

Then run:

```bash
python -m py_compile app/main.py app/workers.py app/media_tools.py app/repositories.py app/manifest.py
python scripts/check_install.py
mkdir -p tmp
touch tmp/restart.txt
curl --max-time 20 -i https://socialmedia.freshcrackers.cz/health
```

Expected health version:

```json
{"app":"Linda","version":"0.4.6.9","ok":true,"database":true,"schema":true,"engine_configured":true,"error":null}
```

## P0 image acceptance test

Run after the AI Engine image-generation fix is deployed:

```bash
python scripts/test_engine_image.py --project-id 1
```

The test must fail if Engine returns ordinary text. A successful test must report a real decodable image plus stored/optimization metadata.

Then create/retry one image from Linda UI and let the normal cron process it. To process immediately for testing:

```bash
python scripts/run_worker.py
```

Acceptance:
1. queue job reaches `Dokončeno`, not merely an HTTP-200 Engine response,
2. new media row exists,
3. image opens in post detail,
4. the same image appears in Obsah, Média and Kalendář,
5. thumbnail is present,
6. ordinary opaque output is stored as `.jpg` (or another optimized non-PNG format),
7. a corrupt/text output becomes a human-readable failed job and does not block later jobs.

## Existing PNG cleanup

Preferred production path: **Admin konzole → Diagnostika → Média** and use:
- `Optimalizovat nyní` for one bounded batch,
- `Optimalizovat projekt`,
- `Optimalizovat vše`.

CLI dry-run is also available:

```bash
python scripts/optimize_media.py --limit 20
```

Apply only after reviewing the dry run:

```bash
python scripts/optimize_media.py --apply --limit 20 --delete-source
```

The scheduled maintenance worker continues in small batches so normal Passenger traffic is not overloaded.

## Queue verification

```bash
crontab -l | grep LINDA_BACKGROUND_WORKER
tail -n 100 storage/worker.log
```

Expected cron continues to use the Linda virtualenv Python and run every two minutes.

## Rollback

The update does not intentionally change the DB schema. If application code must be rolled back, restore the previous 0.4.6.8 application files while preserving the current database, `.env` and `storage/uploads`.
