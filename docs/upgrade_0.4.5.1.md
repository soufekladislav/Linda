# Upgrade to Linda 0.4.5.1

Mobile navigation hotfix.

- Removes duplicate mobile menu click handlers from `app.js` and `ux042.js` that toggled the sidebar twice.
- Adds a single canonical mobile menu controller with backdrop, Escape close, link close, resize reset, body scroll lock and ARIA state.
- Strengthens responsive top bar behavior on phones.
- No database, requirements or `.env` changes.
