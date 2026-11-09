<?php
if ( ! defined( 'ABSPATH' ) ) { exit; }

/**
 * Generic content/publishing bridge for Fresh Core 0.5.x.
 *
 * It intentionally contains no Linda-specific namespace. Linda, Boban,
 * Web Booster and future Fresh apps can share the same contract.
 */
final class Fresh_Core_Content_Bridge {
    const CHANGE_LOG_OPTION = 'fresh_core_content_change_log';
    const EXTERNAL_ID_META = '_fresh_core_external_id';

    public static function init(): void {
        add_action( 'rest_api_init', array( __CLASS__, 'routes' ) );
    }

    public static function routes(): void {
        $read = array( 'permission_callback' => array( 'Fresh_Core_Auth', 'permission_or_error' ) );
        $write = array( 'permission_callback' => array( 'Fresh_Core_Auth', 'permission_write_or_error' ) );

        register_rest_route( 'fresh-core/v1', '/content/export', array_merge( $read, array(
            'methods' => 'GET',
            'callback' => array( __CLASS__, 'content_export' ),
            'args' => array(
                'page' => array( 'type' => 'integer', 'default' => 1 ),
                'per_page' => array( 'type' => 'integer', 'default' => 75 ),
            ),
        ) ) );

        register_rest_route( 'fresh-core/v1', '/content/preview-url', array_merge( $read, array(
            'methods' => 'POST',
            'callback' => array( __CLASS__, 'preview_url' ),
        ) ) );

        register_rest_route( 'fresh-core/v1', '/content/status', array_merge( $read, array(
            'methods' => 'GET',
            'callback' => array( __CLASS__, 'content_status' ),
            'args' => array( 'url' => array( 'type' => 'string', 'required' => true ) ),
        ) ) );

        register_rest_route( 'fresh-core/v1', '/write/content/upsert', array_merge( $write, array(
            'methods' => 'POST',
            'callback' => array( __CLASS__, 'upsert' ),
        ) ) );

        register_rest_route( 'fresh-core/v1', '/analytics/summary', array_merge( $read, array(
            'methods' => 'GET',
            'callback' => array( __CLASS__, 'analytics_summary' ),
        ) ) );


        register_rest_route( 'fresh-core/v1', '/commerce/catalog', array_merge( $read, array(
            'methods' => 'GET',
            'callback' => array( __CLASS__, 'commerce_catalog' ),
            'args' => array(
                'page' => array( 'type' => 'integer', 'default' => 1 ),
                'per_page' => array( 'type' => 'integer', 'default' => 50 ),
                'search' => array( 'type' => 'string', 'default' => '' ),
                'category' => array( 'type' => 'string', 'default' => '' ),
                'featured' => array( 'type' => 'boolean', 'default' => false ),
            ),
        ) ) );

        register_rest_route( 'fresh-core/v1', '/commerce/product/(?P<id>\d+)', array_merge( $read, array(
            'methods' => 'GET',
            'callback' => array( __CLASS__, 'commerce_product' ),
        ) ) );
    }

    private static function ok( $data ): WP_REST_Response {
        return new WP_REST_Response( $data, 200 );
    }

    private static function product_enabled(): bool {
        return post_type_exists( 'product' );
    }

    private static function known_local_views( int $id ): int {
        foreach ( array( 'post_views_count', 'views', 'entry_views', '_post_views_count' ) as $key ) {
            $value = get_post_meta( $id, $key, true );
            if ( is_numeric( $value ) ) { return (int) $value; }
        }
        return 0;
    }

    private static function category_catalog(): array {
        $terms = get_terms( array( 'taxonomy' => 'category', 'hide_empty' => false ) );
        if ( is_wp_error( $terms ) ) { return array(); }
        return array_map( static function( $term ) {
            return array(
                'id' => (int) $term->term_id,
                'name' => $term->name,
                'slug' => $term->slug,
                'count' => (int) $term->count,
                'parent' => (int) $term->parent,
            );
        }, $terms );
    }

    private static function term_rows( int $post_id, string $taxonomy ): array {
        if ( ! taxonomy_exists( $taxonomy ) ) { return array(); }
        $terms = wp_get_post_terms( $post_id, $taxonomy );
        if ( is_wp_error( $terms ) ) { return array(); }
        return array_map( static function( $term ) {
            return array(
                'id' => (int) $term->term_id,
                'name' => $term->name,
                'slug' => $term->slug,
                'parent' => (int) $term->parent,
            );
        }, $terms );
    }

    private static function term_names( int $post_id, string $taxonomy ): array {
        if ( ! taxonomy_exists( $taxonomy ) ) { return array(); }
        $terms = wp_get_post_terms( $post_id, $taxonomy, array( 'fields' => 'names' ) );
        return is_wp_error( $terms ) ? array() : array_values( $terms );
    }


    private static function product_payload( int $post_id ): array {
        if ( ! function_exists( 'wc_get_product' ) ) { return array(); }
        $product = wc_get_product( $post_id );
        if ( ! $product ) { return array(); }
        $attributes = array();
        foreach ( $product->get_attributes() as $attribute ) {
            $values = array();
            if ( $attribute->is_taxonomy() ) {
                $terms = wc_get_product_terms( $post_id, $attribute->get_name(), array( 'fields' => 'names' ) );
                $values = is_wp_error( $terms ) ? array() : array_values( $terms );
            } else {
                $values = array_values( $attribute->get_options() );
            }
            $attributes[] = array(
                'name' => wc_attribute_label( $attribute->get_name() ),
                'slug' => $attribute->get_name(),
                'visible' => (bool) $attribute->get_visible(),
                'variation' => (bool) $attribute->get_variation(),
                'values' => $values,
            );
        }
        $gallery = array();
        foreach ( $product->get_gallery_image_ids() as $attachment_id ) {
            $url = wp_get_attachment_image_url( (int) $attachment_id, 'large' );
            if ( $url ) { $gallery[] = $url; }
        }
        return array(
            'product_id' => $post_id,
            'type' => $product->get_type(),
            'sku' => $product->get_sku(),
            'price' => $product->get_price(),
            'regular_price' => $product->get_regular_price(),
            'sale_price' => $product->get_sale_price(),
            'currency' => function_exists( 'get_woocommerce_currency' ) ? get_woocommerce_currency() : '',
            'on_sale' => (bool) $product->is_on_sale(),
            'purchasable' => (bool) $product->is_purchasable(),
            'featured' => (bool) $product->is_featured(),
            'stock_status' => $product->get_stock_status(),
            'stock_quantity' => $product->get_stock_quantity(),
            'average_rating' => $product->get_average_rating(),
            'rating_count' => $product->get_rating_count(),
            'categories' => self::term_rows( $post_id, 'product_cat' ),
            'tags' => self::term_rows( $post_id, 'product_tag' ),
            'attributes' => $attributes,
            'image' => get_the_post_thumbnail_url( $post_id, 'large' ) ?: '',
            'gallery' => $gallery,
        );
    }

    public static function content_export( WP_REST_Request $request ) {
        try {
            $page = max( 1, (int) $request->get_param( 'page' ) );
            $per_page = max( 10, min( 100, (int) $request->get_param( 'per_page' ) ) );
            $types = array( 'post', 'page' );
            if ( self::product_enabled() ) { $types[] = 'product'; }

            $query = new WP_Query( array(
                'post_type' => $types,
                'post_status' => 'publish',
                'posts_per_page' => $per_page,
                'paged' => $page,
                'orderby' => 'modified',
                'order' => 'DESC',
                'suppress_filters' => false,
                'no_found_rows' => false,
            ) );

            $items = array();
            foreach ( $query->posts as $post ) {
                $content = wp_strip_all_tags( strip_shortcodes( (string) $post->post_content ) );
                $excerpt = has_excerpt( $post )
                    ? get_the_excerpt( $post )
                    : wp_trim_words( $content, 45, '' );
                $featured = get_the_post_thumbnail_url( $post->ID, 'large' );
                $seo = class_exists( 'Fresh_Core_SEO' ) ? Fresh_Core_SEO::item( (int) $post->ID ) : array();

                $taxonomies = array();
                foreach ( get_object_taxonomies( $post->post_type ) as $taxonomy ) {
                    $tax_obj = get_taxonomy( $taxonomy );
                    if ( ! $tax_obj || ( ! $tax_obj->public && ! $tax_obj->show_ui ) ) { continue; }
                    if ( in_array( $taxonomy, array( 'nav_menu', 'link_category', 'post_format' ), true ) ) { continue; }
                    $rows = self::term_rows( (int) $post->ID, $taxonomy );
                    if ( $rows ) { $taxonomies[ $taxonomy ] = $rows; }
                }

                $category_tax = 'product' === $post->post_type ? 'product_cat' : 'category';
                $tag_tax = 'product' === $post->post_type ? 'product_tag' : 'post_tag';
                $items[] = array(
                    'id' => (int) $post->ID,
                    'type' => $post->post_type,
                    'status' => $post->post_status,
                    'title' => get_the_title( $post ),
                    'url' => get_permalink( $post ),
                    'slug' => $post->post_name,
                    'excerpt' => wp_strip_all_tags( (string) $excerpt ),
                    'content' => function_exists( 'mb_substr' ) ? mb_substr( $content, 0, 60000 ) : substr( $content, 0, 60000 ),
                    'featured_image' => $featured ?: '',
                    // Compatibility with Linda 3.8 project_knowledge metadata.
                    'categories' => self::term_names( (int) $post->ID, $category_tax ),
                    'tags' => self::term_names( (int) $post->ID, $tag_tax ),
                    'taxonomies' => $taxonomies,
                    'seo' => is_array( $seo ) ? ( $seo['seo'] ?? array() ) : array(),
                    'local_views' => self::known_local_views( (int) $post->ID ),
                    'modified' => get_post_modified_time( 'c', true, $post ),
                    'commerce' => 'product' === $post->post_type ? self::product_payload( (int) $post->ID ) : array(),
                );
            }

            return self::ok( array(
                'ok' => true,
                'site' => get_bloginfo( 'name' ),
                'home' => home_url( '/' ),
                'page' => $page,
                'per_page' => $per_page,
                'total' => (int) $query->found_posts,
                'total_pages' => (int) $query->max_num_pages,
                'has_more' => $page < (int) $query->max_num_pages,
                'categories' => 1 === $page ? self::category_catalog() : array(),
                'items' => $items,
            ) );
        } catch ( Throwable $e ) {
            return new WP_Error( 'fresh_core_content_export', $e->getMessage(), array( 'status' => 500 ) );
        }
    }

    public static function preview_url( WP_REST_Request $request ) {
        $payload = $request->get_json_params();
        if ( ! is_array( $payload ) ) { $payload = array(); }
        $post_type = sanitize_key( (string) ( $payload['post_type'] ?? 'post' ) );
        if ( ! in_array( $post_type, array( 'post', 'page' ), true ) ) {
            return new WP_Error( 'fresh_core_bad_post_type', 'Unsupported post type.', array( 'status' => 400 ) );
        }
        $slug = sanitize_title( (string) ( $payload['slug'] ?? '' ) );
        if ( '' === $slug ) {
            return new WP_Error( 'fresh_core_missing_slug', 'Missing slug.', array( 'status' => 400 ) );
        }

        if ( 'page' === $post_type ) {
            $url = home_url( user_trailingslashit( $slug ) );
        } else {
            $structure = (string) get_option( 'permalink_structure', '' );
            $now = current_time( 'timestamp' );
            if ( $structure && false !== strpos( $structure, '%postname%' ) ) {
                $path = str_replace(
                    array( '%year%', '%monthnum%', '%day%', '%hour%', '%minute%', '%second%', '%post_id%', '%postname%', '%category%', '%author%' ),
                    array( date( 'Y', $now ), date( 'm', $now ), date( 'd', $now ), date( 'H', $now ), date( 'i', $now ), date( 's', $now ), '0', $slug, '', $slug ),
                    $structure
                );
                $url = home_url( user_trailingslashit( $path ) );
            } else {
                $url = home_url( '/' . $slug . '/' );
            }
        }

        return self::ok( array( 'ok' => true, 'url' => $url, 'slug' => $slug, 'post_type' => $post_type, 'preview' => true ) );
    }

    public static function content_status( WP_REST_Request $request ) {
        $url = esc_url_raw( (string) $request->get_param( 'url' ) );
        if ( '' === $url ) {
            return new WP_Error( 'fresh_core_missing_url', 'Missing URL.', array( 'status' => 400 ) );
        }

        $target = wp_parse_url( $url );
        $home = wp_parse_url( home_url( '/' ) );
        $target_host = preg_replace( '/^www\\./i', '', strtolower( (string) ( $target['host'] ?? '' ) ) );
        $home_host = preg_replace( '/^www\\./i', '', strtolower( (string) ( $home['host'] ?? '' ) ) );
        if ( ! $target_host || ! $home_host || $target_host !== $home_host ) {
            return self::ok( array( 'ok' => true, 'visible' => false, 'message' => 'URL does not belong to this WordPress site.' ) );
        }

        if ( untrailingslashit( strtok( $url, '?' ) ) === untrailingslashit( home_url( '/' ) ) ) {
            return self::ok( array( 'ok' => true, 'visible' => true, 'post_id' => 0, 'post_type' => 'home', 'status' => 'publish', 'url' => home_url( '/' ) ) );
        }

        $post_id = (int) url_to_postid( $url );
        if ( ! $post_id ) {
            return self::ok( array( 'ok' => true, 'visible' => false, 'message' => 'No WordPress content found for URL.' ) );
        }

        $status = get_post_status( $post_id );
        return self::ok( array(
            'ok' => true,
            'visible' => 'publish' === $status,
            'post_id' => $post_id,
            'post_type' => get_post_type( $post_id ),
            'status' => $status,
            'url' => get_permalink( $post_id ),
        ) );
    }

    private static function resolve_taxonomies( int $post_id, array $taxonomies ): void {
        foreach ( $taxonomies as $taxonomy => $value ) {
            $taxonomy = sanitize_key( (string) $taxonomy );
            $post_type = get_post_type( $post_id );
            if ( ! taxonomy_exists( $taxonomy ) || ! $post_type || ! is_object_in_taxonomy( $post_type, $taxonomy ) ) { continue; }
            $terms = array();
            foreach ( (array) $value as $term ) {
                if ( is_numeric( $term ) ) { $terms[] = (int) $term; }
                elseif ( is_scalar( $term ) ) { $terms[] = sanitize_text_field( (string) $term ); }
            }
            wp_set_object_terms( $post_id, $terms, $taxonomy, false );
        }
    }

    private static function sideload_featured_image( int $post_id, string $url ): ?int {
        if ( '' === $url || ! wp_http_validate_url( $url ) ) { return null; }
        require_once ABSPATH . 'wp-admin/includes/media.php';
        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/image.php';
        $attachment_id = media_sideload_image( $url, $post_id, null, 'id' );
        if ( is_wp_error( $attachment_id ) ) { return null; }
        set_post_thumbnail( $post_id, (int) $attachment_id );
        return (int) $attachment_id;
    }

    private static function change_log(): array {
        $log = get_option( self::CHANGE_LOG_OPTION, array() );
        return is_array( $log ) ? $log : array();
    }

    private static function find_change( string $change_id ): ?array {
        if ( '' === $change_id ) { return null; }
        foreach ( self::change_log() as $entry ) {
            if ( is_array( $entry ) && (string) ( $entry['change_id'] ?? '' ) === $change_id ) { return $entry; }
        }
        return null;
    }

    private static function log_change( array $entry ): void {
        $log = self::change_log();
        array_unshift( $log, $entry );
        update_option( self::CHANGE_LOG_OPTION, array_slice( $log, 0, 1000 ), false );
    }

    private static function response_for_post( int $post_id, array $extra = array() ): array {
        return array_merge( array(
            'ok' => true,
            'post_id' => $post_id,
            'post_type' => get_post_type( $post_id ),
            'status' => get_post_status( $post_id ),
            'url' => get_permalink( $post_id ),
            'edit_url' => get_edit_post_link( $post_id, 'raw' ),
            'featured_media_id' => (int) get_post_thumbnail_id( $post_id ),
            'modified' => get_post_modified_time( 'c', true, $post_id ),
        ), $extra );
    }

    private static function post_by_external_id( string $external_id, string $post_type ): int {
        if ( '' === $external_id ) { return 0; }
        $ids = get_posts( array(
            'post_type' => $post_type,
            'post_status' => array( 'publish', 'draft', 'pending', 'private', 'future' ),
            'posts_per_page' => 1,
            'fields' => 'ids',
            'meta_key' => self::EXTERNAL_ID_META,
            'meta_value' => $external_id,
            'suppress_filters' => true,
        ) );
        return $ids ? (int) $ids[0] : 0;
    }

    public static function upsert( WP_REST_Request $request ) {
        $payload = $request->get_json_params();
        if ( ! is_array( $payload ) ) { $payload = array(); }

        $change_id = sanitize_text_field( (string) ( $payload['change_id'] ?? '' ) );
        $external_id = sanitize_text_field( (string) ( $payload['external_id'] ?? '' ) );
        $source_module = sanitize_key( (string) ( $payload['source_module'] ?? 'content_bridge' ) );
        if ( '' === $source_module ) { $source_module = 'content_bridge'; }
        if ( strlen( $change_id ) > 190 ) { $change_id = substr( $change_id, 0, 190 ); }
        if ( strlen( $external_id ) > 190 ) { $external_id = substr( $external_id, 0, 190 ); }

        if ( '' !== $change_id ) {
            $previous = self::find_change( $change_id );
            $previous_id = (int) ( $previous['post_id'] ?? 0 );
            if ( $previous_id > 0 && get_post( $previous_id ) ) {
                return self::ok( self::response_for_post( $previous_id, array(
                    'duplicate' => true,
                    'change_id' => $change_id,
                    'external_id' => (string) ( $previous['external_id'] ?? '' ),
                    'original_time_gmt' => (string) ( $previous['time_gmt'] ?? '' ),
                ) ) );
            }
        }

        $post_type = sanitize_key( (string) ( $payload['post_type'] ?? 'post' ) );
        if ( ! in_array( $post_type, array( 'post', 'page' ), true ) ) {
            return new WP_Error( 'fresh_core_bad_post_type', 'Only post and page are allowed.', array( 'status' => 400 ) );
        }

        $post_id = (int) ( $payload['post_id'] ?? 0 );
        if ( ! $post_id && '' !== $external_id ) { $post_id = self::post_by_external_id( $external_id, $post_type ); }
        if ( $post_id && get_post_type( $post_id ) !== $post_type ) {
            return new WP_Error( 'fresh_core_bad_post', 'Existing ID has a different post type.', array( 'status' => 400 ) );
        }

        $status = sanitize_key( (string) ( $payload['status'] ?? 'draft' ) );
        if ( ! in_array( $status, array( 'draft', 'publish', 'pending' ), true ) ) { $status = 'draft'; }

        $data = array(
            'post_type' => $post_type,
            'post_status' => $status,
            'post_title' => sanitize_text_field( (string) ( $payload['title'] ?? 'Untitled' ) ),
            'post_content' => wp_kses_post( (string) ( $payload['content'] ?? '' ) ),
            'post_excerpt' => sanitize_textarea_field( (string) ( $payload['excerpt'] ?? '' ) ),
        );
        if ( '' !== (string) ( $payload['slug'] ?? '' ) ) {
            $data['post_name'] = sanitize_title( (string) $payload['slug'] );
        }
        if ( $post_id ) { $data['ID'] = $post_id; }

        $saved_id = wp_insert_post( $data, true );
        if ( is_wp_error( $saved_id ) ) {
            return new WP_Error( 'fresh_core_content_upsert', $saved_id->get_error_message(), array( 'status' => 500 ) );
        }
        $saved_id = (int) $saved_id;
        if ( '' !== $external_id ) { update_post_meta( $saved_id, self::EXTERNAL_ID_META, $external_id ); }

        if ( isset( $payload['taxonomies'] ) && is_array( $payload['taxonomies'] ) ) {
            self::resolve_taxonomies( $saved_id, $payload['taxonomies'] );
        }

        $featured_media_id = (int) ( $payload['featured_media_id'] ?? 0 );
        if ( $featured_media_id && 'attachment' === get_post_type( $featured_media_id ) ) {
            set_post_thumbnail( $saved_id, $featured_media_id );
        } elseif ( ! has_post_thumbnail( $saved_id ) && ! empty( $payload['featured_image_url'] ) ) {
            $featured_media_id = (int) ( self::sideload_featured_image( $saved_id, esc_url_raw( (string) $payload['featured_image_url'] ) ) ?: 0 );
        }

        if ( class_exists( 'Fresh_Core_Export' ) && method_exists( 'Fresh_Core_Export', 'mark_dirty_simple' ) ) {
            Fresh_Core_Export::mark_dirty_simple();
        }

        self::log_change( array(
            'change_id' => $change_id,
            'external_id' => $external_id,
            'post_id' => $saved_id,
            'post_type' => $post_type,
            'status' => get_post_status( $saved_id ),
            'source_module' => $source_module,
            'time_gmt' => gmdate( 'c' ),
        ) );
        if ( class_exists( 'Fresh_Core_Modules' ) ) {
            Fresh_Core_Modules::emit( 'content.upserted', array(
                'post_id' => $saved_id,
                'post_type' => $post_type,
                'status' => get_post_status( $saved_id ),
                'change_id' => $change_id,
                'external_id' => $external_id,
            ), $source_module );
        }

        return self::ok( self::response_for_post( $saved_id, array(
            'duplicate' => false,
            'change_id' => $change_id,
            'external_id' => $external_id,
            'featured_media_id' => $featured_media_id ?: (int) get_post_thumbnail_id( $saved_id ),
        ) ) );
    }

    public static function commerce_catalog( WP_REST_Request $request ) {
        if ( ! self::product_enabled() || ! function_exists( 'wc_get_product' ) ) {
            return self::ok( array( 'ok' => true, 'available' => false, 'items' => array(), 'total' => 0, 'total_pages' => 0 ) );
        }
        $page = max( 1, (int) $request->get_param( 'page' ) );
        $per_page = max( 1, min( 100, (int) $request->get_param( 'per_page' ) ) );
        $search = sanitize_text_field( (string) $request->get_param( 'search' ) );
        $category = sanitize_title( (string) $request->get_param( 'category' ) );
        $featured = rest_sanitize_boolean( $request->get_param( 'featured' ) );
        $args = array(
            'post_type' => 'product', 'post_status' => 'publish', 'posts_per_page' => $per_page,
            'paged' => $page, 'orderby' => 'modified', 'order' => 'DESC', 'suppress_filters' => false,
        );
        if ( '' !== $search ) { $args['s'] = $search; }
        if ( '' !== $category ) {
            $args['tax_query'] = array( array( 'taxonomy' => 'product_cat', 'field' => 'slug', 'terms' => array( $category ) ) );
        }
        if ( $featured && function_exists( 'wc_get_featured_product_ids' ) ) {
            $ids = wc_get_featured_product_ids();
            $args['post__in'] = $ids ? $ids : array( 0 );
        }
        $query = new WP_Query( $args );
        $items = array();
        foreach ( $query->posts as $post ) {
            $product = self::product_payload( (int) $post->ID );
            $product['title'] = get_the_title( $post );
            $product['url'] = get_permalink( $post );
            $product['slug'] = $post->post_name;
            $product['excerpt'] = wp_strip_all_tags( has_excerpt( $post ) ? get_the_excerpt( $post ) : wp_trim_words( wp_strip_all_tags( $post->post_content ), 35, '' ) );
            $product['modified'] = get_post_modified_time( 'c', true, $post );
            $items[] = $product;
        }
        return self::ok( array(
            'ok' => true, 'available' => true, 'page' => $page, 'per_page' => $per_page,
            'total' => (int) $query->found_posts, 'total_pages' => (int) $query->max_num_pages,
            'has_more' => $page < (int) $query->max_num_pages, 'items' => $items,
        ) );
    }

    public static function commerce_product( WP_REST_Request $request ) {
        $post_id = (int) $request->get_param( 'id' );
        if ( $post_id <= 0 || 'product' !== get_post_type( $post_id ) || 'publish' !== get_post_status( $post_id ) ) {
            return new WP_Error( 'fresh_core_product_not_found', 'Product not found.', array( 'status' => 404 ) );
        }
        $item = self::product_payload( $post_id );
        if ( ! $item ) { return new WP_Error( 'fresh_core_woocommerce_missing', 'WooCommerce is not available.', array( 'status' => 404 ) ); }
        $post = get_post( $post_id );
        $item['title'] = get_the_title( $post_id );
        $item['url'] = get_permalink( $post_id );
        $item['slug'] = $post ? $post->post_name : '';
        $item['excerpt'] = $post ? wp_strip_all_tags( has_excerpt( $post ) ? get_the_excerpt( $post ) : wp_trim_words( wp_strip_all_tags( $post->post_content ), 45, '' ) ) : '';
        $item['content'] = $post ? wp_strip_all_tags( strip_shortcodes( (string) $post->post_content ) ) : '';
        return self::ok( array( 'ok' => true, 'available' => true, 'item' => $item ) );
    }

    private static function active_plugin_like( string $needle ): bool {
        $active = (array) get_option( 'active_plugins', array() );
        foreach ( $active as $plugin ) {
            if ( false !== stripos( (string) $plugin, $needle ) ) { return true; }
        }
        if ( is_multisite() ) {
            foreach ( (array) get_site_option( 'active_sitewide_plugins', array() ) as $plugin => $value ) {
                if ( false !== stripos( (string) $plugin, $needle ) ) { return true; }
            }
        }
        return false;
    }

    public static function analytics_summary( WP_REST_Request $request ) {
        $integrations = array(
            'site_kit' => defined( 'GOOGLESITEKIT_VERSION' ) || self::active_plugin_like( 'google-site-kit' ),
            'monsterinsights' => defined( 'MONSTERINSIGHTS_VERSION' ) || self::active_plugin_like( 'google-analytics-for-wordpress' ),
            'woocommerce' => class_exists( 'WooCommerce' ),
        );

        $woo = array( 'available' => false );
        if ( class_exists( 'WooCommerce' ) && function_exists( 'wc_get_orders' ) ) {
            $from = ( new DateTime( '-30 days', wp_timezone() ) )->format( 'Y-m-d H:i:s' );
            $orders = wc_get_orders( array(
                'limit' => -1,
                'status' => array( 'wc-processing', 'wc-completed' ),
                'date_created' => '>=' . $from,
                'return' => 'objects',
            ) );
            $revenue = 0.0;
            foreach ( $orders as $order ) { $revenue += (float) $order->get_total(); }
            $woo = array(
                'available' => true,
                'period_days' => 30,
                'orders' => count( $orders ),
                'revenue' => $revenue,
                'currency' => get_woocommerce_currency(),
            );
        }

        return self::ok( array( 'ok' => true, 'integrations' => $integrations, 'woocommerce' => $woo ) );
    }
}
