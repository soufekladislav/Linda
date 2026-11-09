# Upgrade Linda 0.4.0 → 0.4.1

## Preserve
- `.env`
- `storage/uploads/`
- production database
- cPanel Python application / virtualenv

## Files to replace
The update ZIP replaces `app/`, `templates/`, `requirements.txt`, `passenger_wsgi.py`, `startup.py`, `version` and documentation. Original assets are included only if changed; the current CSS/JS is intentionally preserved.

## Commands
```bash
source /home2/lauhannq/virtualenv/socialmedia.freshcrackers.cz/3.11/bin/activate
cd /home2/lauhannq/socialmedia.freshcrackers.cz
pip install -r requirements.txt
python -m scripts.check_install
touch tmp/restart.txt
curl --max-time 15 -i https://socialmedia.freshcrackers.cz/health
```

If cPanel recreates `passenger_wsgi.py`, restore the packaged file. The old wrapper that imports `passenger_wsgi.py` from itself causes recursion and must not be used.
