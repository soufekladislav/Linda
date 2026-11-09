# Linda 3.8 → Python 0.4.1 parity

## Restored
- Existing users/projects/database and media routes
- Dashboard/action center
- Project setup, knowledge sync and project profile
- Brand kit and Style DNA analysis
- Marketing activities
- Content library, editing, duplication, deletion and bulk approval
- Calendar and scheduled publication dispatcher
- Article library and approval
- Article-first generation and social derivatives
- Smart links, UTM and pre-publication destination checks
- WordPress/Fresh Core connector and publication worker
- Social connector test/preflight and direct publication workers
- Meta OAuth flow
- Media library, free image, post/blog image and carousel jobs
- Analytics sync
- Notifications/preferences
- Users/roles/impersonation
- Legacy queue aliases: sitemap_async, batch_group, image_generate_carousel_slide, free_image_generate

## External prerequisites
- Fresh Core 0.5 write/status/analytics routes are required for full WordPress publishing parity.
- Fresh AI Engine must accept linda_brand_vision and linda_image_generate for vision/image tasks.
- Each social provider still requires valid account permissions/tokens. Provider APIs can reject a request even when Linda's local workflow is correct.

## Deliberately deferred hardening
- encryption-at-rest migration for legacy connector secrets
- replacing legacy DB schema with a new normalized schema (not desired during parity)

## 0.4.3 additions
- Interactive Linda chat is synchronous and no longer depends on cron/task queue.
- Dedicated `linda_chat` Engine task with compatibility fallback.
- Engine run audit persisted in `ai_events` and surfaced in developer manifest.
- Stronger legacy media path recovery and cached thumbnails.
- Automatic optimization for newly generated/uploaded images.
- Explicit batch scripts for old-media optimization and orphan cleanup.
- Linda-first branding on login and inside the workspace.
