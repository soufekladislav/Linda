from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import settings


@dataclass
class FreshCoreResponse:
    ok: bool
    status_code: int
    data: Any
    error: str | None = None


class FreshCoreClient:
    """
    Universal WordPress bridge used by Linda.

    Connector row mapping (no DB migration):
      endpoint_url -> WordPress site root
      access_token -> Fresh Core READ key
      client_secret -> Fresh Core WRITE key

    During rollout the optional legacy fallback can still call the old
    fresh-ai-social/v1 namespace, but Linda 0.4.0 is designed for Fresh Core.
    """

    def __init__(self, site_url: str, read_key: str = "", write_key: str = "") -> None:
        self.site_url = site_url.rstrip("/")
        self.read_key = read_key
        self.write_key = write_key
        self.base = f"{self.site_url}/wp-json/fresh-core/v1"
        self.legacy_base = f"{self.site_url}/wp-json/fresh-ai-social/v1"

    def _read_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "x-fresh-core-key": self.read_key}

    def _write_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-fresh-core-write-key": self.write_key,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        write: bool = False,
        timeout: int = 45,
    ) -> FreshCoreResponse:
        url = self.base + path
        headers = self._write_headers() if write else self._read_headers()
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                r = client.request(method, url, headers=headers, params=params, json=json)
                # Some WordPress/security stacks strip custom headers or reject
                # anonymous REST before the Fresh Core permission callback. For
                # READ requests Fresh Core also supports the scoped key as a
                # query parameter. Retry once so connector tests remain robust.
                if (not write) and self.read_key and r.status_code in {401, 403}:
                    retry_params = dict(params or {})
                    retry_params["fresh_core_key"] = self.read_key
                    r = client.request(method, url, headers={"Accept": "application/json"}, params=retry_params, json=json)
            try:
                data = r.json()
            except Exception:
                data = r.text[:5000]
            return FreshCoreResponse(r.is_success, r.status_code, data, None if r.is_success else str(data))
        except Exception as exc:
            return FreshCoreResponse(False, 0, None, str(exc))

    def ping(self) -> FreshCoreResponse:
        return self._request("GET", "/ping")

    def manifest(self) -> FreshCoreResponse:
        return self._request("GET", "/manifest")

    def content(
        self,
        post_type: str,
        *,
        page: int = 1,
        per_page: int = 100,
        include_content: bool = True,
        status: str = "publish",
    ) -> FreshCoreResponse:
        return self._request(
            "GET",
            "/content",
            params={
                "post_type": post_type,
                "page": page,
                "per_page": per_page,
                "include_content": "1" if include_content else "0",
                "status": status,
            },
        )

    def terms(self, taxonomy: str, *, page: int = 1, per_page: int = 1000) -> FreshCoreResponse:
        return self._request(
            "GET",
            "/terms",
            params={"taxonomy": taxonomy, "page": page, "per_page": per_page},
        )

    def content_export(self, *, page: int = 1, per_page: int = 75) -> FreshCoreResponse:
        """Fresh Core 0.5+ mixed content export used for full Linda sync parity."""
        return self._request(
            "GET",
            "/content/export",
            params={"page": page, "per_page": per_page},
        )

    def seo_item(self, post_id: int) -> FreshCoreResponse:
        return self._request("GET", "/seo/item", params={"id": post_id})

    # Routes proposed for Fresh Core 0.5.0; see docs/FRESH_CORE_0.5_CONTRACT.md.
    def preview_url(self, slug: str, post_type: str = "post") -> FreshCoreResponse:
        return self._request(
            "POST",
            "/content/preview-url",
            json={"slug": slug, "post_type": post_type},
        )

    def content_status(self, url: str) -> FreshCoreResponse:
        return self._request("GET", "/content/status", params={"url": url})

    def upsert_content(self, payload: dict[str, Any]) -> FreshCoreResponse:
        return self._request("POST", "/write/content/upsert", json=payload, write=True, timeout=90)

    def apply_seo(self, payload: dict[str, Any]) -> FreshCoreResponse:
        return self._request("POST", "/seo/apply", json=payload, write=True)

    def analytics_summary(self) -> FreshCoreResponse:
        return self._request("GET", "/analytics/summary")

    def commerce_catalog(self, *, page: int = 1, per_page: int = 50, search: str = "", category: str = "") -> FreshCoreResponse:
        return self._request(
            "GET", "/commerce/catalog",
            params={"page": page, "per_page": per_page, "search": search, "category": category},
        )

    def commerce_product(self, product_id: int) -> FreshCoreResponse:
        return self._request("GET", f"/commerce/product/{int(product_id)}")

    def legacy_request(
        self,
        method: str,
        route: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: int = 60,
    ) -> FreshCoreResponse:
        if not settings.fresh_core_legacy_fallback:
            return FreshCoreResponse(False, 0, None, "Legacy fallback je vypnutý.")
        headers = {"Accept": "application/json", "x-fresh-ai-token": self.read_key}
        if json is not None:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                r = client.request(
                    method,
                    self.legacy_base + "/" + route.lstrip("/"),
                    headers=headers,
                    params=params,
                    json=json,
                )
            try:
                data = r.json()
            except Exception:
                data = r.text[:5000]
            return FreshCoreResponse(r.is_success, r.status_code, data, None if r.is_success else str(data))
        except Exception as exc:
            return FreshCoreResponse(False, 0, None, str(exc))


def from_connector(connector: dict[str, Any]) -> FreshCoreClient:
    return FreshCoreClient(
        str(connector.get("endpoint_url") or ""),
        str(connector.get("access_token") or ""),
        str(connector.get("client_secret") or ""),
    )
