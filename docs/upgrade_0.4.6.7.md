# Upgrade Linda 0.4.6.6 → 0.4.6.7

No database migration and no new Python dependency. Preserve `.env` and `storage/uploads`.

After extracting the update:

```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz
python -m py_compile app/*.py scripts/*.py
touch tmp/restart.txt
curl --max-time 20 -i https://socialmedia.freshcrackers.cz/health
```

Expected version: `0.4.6.7`. The existing 2-minute CLI cron remains valid.
