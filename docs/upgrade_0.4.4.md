# Upgrade 0.4.3 → 0.4.4

- Replace application code only; keep `.env` and `storage/uploads`.
- `requirements.txt` is compatible with 0.4.3 and still requires Pillow.
- Update Fresh Core to 0.5.2 on connected WordPress sites.
- Restart Passenger and verify `/health` and the developer manifest.
- Chat no longer depends on task queue/cron; long content/media/publication tasks still do.
- Keep a maintenance cron enabled so Linda can gradually optimize legacy media without large one-shot CPU/RAM spikes.
