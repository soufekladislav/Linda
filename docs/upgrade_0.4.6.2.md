# Linda 0.4.6.2

Production hotfix for WordPress/web-content workflow.

- public media URLs now end in a real image extension so WordPress `media_sideload_image()` can attach Linda media as featured images,
- post editor can regenerate text with Fresh AI Engine without replacing the current image,
- legacy `platform=blog` rows are automatically expanded into proper WordPress articles before first publish if the body is too short,
- editor can verify/fill a destination URL; social publication tries to recover a missing conversion URL automatically,
- WordPress categories are loaded from Fresh Core and Linda suggests a category from synchronized site content; user can override it before publish,
- duplicate is demoted to a secondary "variant" workflow and now preserves the latest linked media,
- featured-image sideload is validated after Fresh Core upsert instead of silently accepting a post without its image.

No DB migration. Preserve `.env` and `storage/uploads`.
