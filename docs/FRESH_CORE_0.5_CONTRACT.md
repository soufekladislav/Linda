# Fresh Core 0.5.2 contract used by Linda 0.4.4

Read key (`x-fresh-core-key`):
- GET `/wp-json/fresh-core/v1/ping`
- GET `/wp-json/fresh-core/v1/content/export`
- POST `/wp-json/fresh-core/v1/content/preview-url`
- GET `/wp-json/fresh-core/v1/content/status`
- GET `/wp-json/fresh-core/v1/analytics/summary`
- GET `/wp-json/fresh-core/v1/commerce/catalog`
- GET `/wp-json/fresh-core/v1/commerce/product/<id>`

Write key (`x-fresh-core-write-key`):
- POST `/wp-json/fresh-core/v1/write/content/upsert`
- existing protected SEO write endpoints

Linda stores the WordPress site root in `social_connectors.endpoint_url`, the read key in `access_token`, and the write key in `client_secret` for legacy DB compatibility.

`content/export` is the primary knowledge sync endpoint. In Fresh Core 0.5.2 WooCommerce product rows additionally include `commerce` metadata (price, sale price, stock, category, rating, featured image and gallery). Linda persists that metadata in `project_knowledge.meta_json` so planning can work from real products and URLs.
