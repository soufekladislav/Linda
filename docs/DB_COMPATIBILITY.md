# Database compatibility – Linda 0.4.0

The Python rewrite connects **directly to the current Linda database**.

No export/import, no project ID remapping and no second database are required.

## First boot policy

1. connect to the existing DB;
2. run a read-only schema compatibility check;
3. refuse to claim unsupported jobs if required legacy columns are missing;
4. do not run automatic `CREATE/ALTER/DROP` statements.

The existing PHP `users.password_hash` remains valid. Python normalizes PHP `$2y$` bcrypt hashes to `$2b$` for verification. Existing accounts therefore keep their passwords.

## Data preserved in-place

- users
- projects and AI/brand fields
- marketing activities
- project knowledge
- content plans and topics
- social posts
- blog posts
- media assets and visual references
- connectors
- queue/history
- analytics
- notifications/action items/audit data

## Sessions

PHP file sessions are not data records and are intentionally not migrated. At cutover, users sign in once; accounts and passwords are unchanged.

## Recommended production cutover

Before switching the domain:
- create a DB backup,
- stop the legacy PHP cron for the cutover minute,
- deploy Python,
- run `python scripts/check_install.py`,
- verify `/healthz`,
- verify the developer manifest,
- start the same cron URLs against the Python app.

Do not run PHP and Python workers concurrently against the same queue.
