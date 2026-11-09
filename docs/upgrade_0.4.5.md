# Upgrade to Linda 0.4.5

- Preserve `.env` and `storage/uploads`.
- Overlay the update ZIP.
- Run `pip install -r requirements.txt`.
- Run `python scripts/check_install.py`.
- Restart Passenger with `touch tmp/restart.txt`.
- Verify `https://socialmedia.freshcrackers.cz/health`.
- Run `python scripts/test_engine.py --project-id 1` once to verify a real chat route through Fresh AI Engine.

Optional Google Business Profile OAuth environment keys:

```env
GOOGLE_BUSINESS_CLIENT_ID=
GOOGLE_BUSINESS_CLIENT_SECRET=
```

The primary user navigation is intentionally reduced. Legacy direct pages still exist where needed for compatibility/editing, but Activities, separate Articles and separate Approval views are no longer primary navigation destinations.
