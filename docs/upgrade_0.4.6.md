# Upgrade Linda 0.4.5.1 → 0.4.6

- Preserve `.env` and `storage/uploads`.
- No database migration.
- No new requirements beyond 0.4.5.1.
- Fresh Core 0.5.2 remains compatible.
- Restart Passenger after copying files.
- Run `python scripts/check_install.py` and confirm `/health` reports 0.4.6.

## Smoke test

1. Send one chat message and verify a Linda reply.
2. Open Calendar: default view should start today and show 14 days.
3. Drag one unpublished card to a future day (prefer a test item).
4. Test the WordPress connector and queue one legacy Blog post for publication.
5. Open the notification bell and Admin → Fronta všech uživatelů.
6. Test mobile menu at ~390 px width.
