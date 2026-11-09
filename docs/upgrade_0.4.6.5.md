# Upgrade Linda 0.4.6.4 → 0.4.6.5

Production queue/approval UX hotfix.

- Failed jobs now keep a human-readable Czech reason in `task_queue.message`; raw diagnostics stay in `result_json`.
- Final publication failures place the affected post on `worker_error` hold so the scheduler does not recreate the same job every two minutes.
- Manual retry clears the hold.
- Structured Engine response parsing accepts nested Router/provider envelopes; WordPress rewrite has a safe fallback.
- Content and calendar show queued/running work directly on the affected post and refresh after completion.
- Approval view is a compact list instead of cards; checkbox sizing is normalized.
- Queue separates active and failed work and exposes a repair CTA; superadmin keeps technical detail.
