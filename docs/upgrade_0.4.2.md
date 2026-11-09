# Upgrade 0.4.1 → 0.4.2

- Preserve `.env`.
- Preserve the whole `storage/uploads` directory.
- Overwrite application code/templates/assets with the 0.4.2 update.
- No SQL migration is required.
- Existing `app_meta` stores workflow and format configuration.
- Install/update requirements, run `scripts/check_install.py`, restart Passenger.
- Update WordPress sites to Fresh Core 0.5.1 when you want product/commerce enrichment and the complete write/analytics bridge.

After deployment verify:
1. login
2. project selector + Linda chat
3. calendar network columns
4. an existing post with an old generated image
5. media library
6. project Fresh Core sync
7. superadmin console
