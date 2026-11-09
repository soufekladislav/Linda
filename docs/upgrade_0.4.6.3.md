# Upgrade Linda 0.4.6.2 → 0.4.6.3

## Purpose

Production hotfix for the background queue, worker observability, completion notifications, proactive planned tasks, and manual Calendar content creation.

## Deploy

```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz
python scripts/check_install.py
touch tmp/restart.txt
curl --max-time 20 -i https://socialmedia.freshcrackers.cz/health
```

## Enable the background worker

Preview only:

```bash
python scripts/install_cron.py
```

Install:

```bash
python scripts/install_cron.py --install
```

The installed cron runs every two minutes and uses the application's MariaDB lock so overlapping worker batches are not executed.

Process the current backlog immediately if desired:

```bash
python scripts/run_worker.py
```

## Fresh AI Engine image smoke test

```bash
python scripts/test_engine_image.py --project-id 1
```

This directly calls `general / image.generate`; it does not depend on cron or `task_queue`.
