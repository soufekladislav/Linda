# Upgrade Linda 0.4.6.7 → 0.4.6.8

Production media hotfix:

- accepts the full Fresh AI Engine 0.6.x image response envelope, including images/artifacts/data/MIME+base64 forms,
- image failures store run_id and response-shape diagnostics,
- generated post/blog/free images always pass through the optimizer before storage,
- opaque RGBA PNGs are converted to JPEG instead of being retained as multi-megabyte PNGs,
- bounded media maintenance now optimizes both media_assets and visual_references,
- optimize_media.py handles both the content library and Brand Kit references.

No schema or requirements change. Existing .env and storage/uploads must be preserved.
