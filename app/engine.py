from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings


@dataclass
class EngineResult:
    ok: bool
    status: str
    run_id: str | None
    provider: str | None
    model: str | None
    result: Any
    result_type: str | None
    validated: bool | None
    input_tokens: int | None
    output_tokens: int | None
    internal_cost: float | None
    credits_charged: float | int | None
    latency_ms: int | None
    http_status: int | None
    route_reason: Any
    raw: dict[str, Any]
    error: str | None = None

    def audit(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "result_type": self.result_type,
            "result_validated": self.validated,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "internal_cost": self.internal_cost,
            "credits_charged": self.credits_charged,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "route_reason": self.route_reason,
            "error": self.error,
        }


class FreshAIEngine:
    def __init__(self) -> None:
        self.base_url = settings.engine_url
        self.api_key = settings.engine_api_key
        self.timeout = settings.engine_timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                r = client.get(f"{self.base_url}/v1/health", headers=self._headers())
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {"ok": r.is_success, "http": r.status_code, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def manifest(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                r = client.get(f"{self.base_url}/v1/manifest", headers=self._headers())
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 404:
                return {"ok": True, "available": False, "http": 404, "note": "Engine /v1/manifest není veřejný endpoint; provoz se ověřuje přes /v1/health a run audit.", "data": data}
            return {"ok": r.is_success, "available": True, "http": r.status_code, "data": data}
        except Exception as exc:
            return {"ok": False, "available": False, "error": str(exc)}

    @staticmethod
    def _pick(data: dict[str, Any], *names: str) -> Any:
        for name in names:
            if name in data and data[name] is not None:
                return data[name]
        return None

    @staticmethod
    def _quality(value: str) -> str:
        # Fresh AI Engine Router 2.x accepts these quality profiles.
        aliases = {
            "standard": "balanced",
            "high": "best",
            "quality": "best",
        }
        clean = aliases.get(str(value or "").strip().lower(), str(value or "").strip().lower())
        return clean if clean in {"auto", "economy", "balanced", "best"} else "balanced"

    @staticmethod
    def _output_contract(value: Any) -> Any:
        # Engine 0.5.x expects an output-contract envelope, not a bare JSON Schema.
        if not isinstance(value, dict):
            return value
        if value.get("type") == "json" and ("schema" in value or "schema_id" in value):
            return value
        if value.get("type") in {"object", "array"} or "properties" in value or "items" in value:
            return {"type": "json", "schema": value}
        return value

    def run(
        self,
        *,
        project_id: int | str | None,
        task_type: str,
        operation: str,
        prompt: str,
        context: Any = None,
        input_data: Any = None,
        output_contract: Any = None,
        quality: str = "balanced",
        max_cost_usd: float | None = None,
    ) -> EngineResult:
        payload: dict[str, Any] = {
            "source": "linda",
            "project_id": str(project_id) if project_id is not None else None,
            "task_type": task_type,
            "operation": operation,
            "quality": self._quality(quality),
            "prompt": prompt,
            "context": context or {},
            "input_data": input_data if isinstance(input_data, dict) else {},
            "output_contract": self._output_contract(output_contract),
        }
        cost_limit = max_cost_usd if max_cost_usd is not None else settings.engine_max_cost_usd
        if cost_limit is not None:
            payload["max_cost_usd"] = cost_limit

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                r = client.post(
                    f"{self.base_url}/v1/engine/run",
                    headers=self._headers(),
                    json=payload,
                )
            elapsed = int((time.perf_counter() - started) * 1000)
            try:
                data = r.json()
            except Exception:
                data = {"raw_text": r.text[:5000]}

            if not r.is_success:
                return EngineResult(
                    ok=False,
                    status="error",
                    run_id=self._pick(data, "run_id", "engine_run_id", "id"),
                    provider=self._pick(data, "provider"),
                    model=self._pick(data, "model"),
                    result=None,
                    result_type=None,
                    validated=None,
                    input_tokens=None,
                    output_tokens=None,
                    internal_cost=None,
                    credits_charged=None,
                    latency_ms=elapsed,
                    http_status=r.status_code,
                    route_reason=self._pick(data, "route_reason", "routing"),
                    raw=data,
                    error=f"Fresh AI Engine HTTP {r.status_code}: {data}",
                )

            result = self._pick(data, "result", "output", "response", "content", "text", "answer", "data")
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            status = str(self._pick(data, "status") or "ok")
            result_type = self._pick(data, "result_type")
            validated = self._pick(data, "result_validated", "validated")
            run_id = self._pick(data, "run_id", "engine_run_id", "job_id", "id")

            # Engine can legitimately answer HTTP 200 while the run itself failed
            # (for example structured_output_invalid). Treat that as a semantic
            # failure so workers do not try to consume a broken result as success.
            semantic_error = None
            status_low = status.strip().lower()
            contract = payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {}
            expects_json = str(contract.get("type") or "").lower() == "json"
            if status_low in {"failed", "error", "invalid", "rejected"}:
                semantic_error = "Fresh AI Engine úlohu nedokončil."
            elif expects_json and validated is False:
                semantic_error = "Fresh AI Engine vrátil neplatný strukturovaný výstup."

            if semantic_error:
                category = str(data.get("error_category") or "").strip()
                detail = ""
                routing = data.get("routing_decision") if isinstance(data.get("routing_decision"), dict) else {}
                oc = routing.get("output_contract") if isinstance(routing.get("output_contract"), dict) else {}
                errors = oc.get("validation_errors") if isinstance(oc.get("validation_errors"), list) else []
                if errors:
                    detail = str(errors[0])[:700]
                elif data.get("error"):
                    detail = str(data.get("error"))[:700]
                bits = [semantic_error]
                if category:
                    bits.append(f"Kategorie: {category}.")
                if detail:
                    bits.append(detail)
                if run_id:
                    bits.append(f"Run ID: {run_id}")
                semantic_error = " ".join(bits)

            return EngineResult(
                ok=semantic_error is None,
                status=status,
                run_id=run_id,
                provider=self._pick(data, "provider"),
                model=self._pick(data, "model"),
                result=result,
                result_type=result_type,
                validated=validated,
                input_tokens=self._pick(data, "input_tokens") or usage.get("input_tokens"),
                output_tokens=self._pick(data, "output_tokens") or usage.get("output_tokens"),
                internal_cost=self._pick(data, "internal_cost", "cost_usd") or usage.get("cost_usd"),
                credits_charged=self._pick(data, "credits_charged", "credits"),
                latency_ms=self._pick(data, "latency_ms") or elapsed,
                http_status=r.status_code,
                route_reason=self._pick(data, "route_reason", "routing"),
                raw=data,
                error=semantic_error,
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return EngineResult(
                ok=False,
                status="error",
                run_id=None,
                provider=None,
                model=None,
                result=None,
                result_type=None,
                validated=None,
                input_tokens=None,
                output_tokens=None,
                internal_cost=None,
                credits_charged=None,
                latency_ms=elapsed,
                http_status=None,
                route_reason=None,
                raw={},
                error=str(exc),
            )


engine_client = FreshAIEngine()


def _decode_json_loose(value: Any) -> Any:
    """Best-effort decoder for provider outputs wrapped in markdown or prose.

    Router 2.x already validates structured output, but some providers still
    return a fenced JSON string or (for object schemas) a one-item list. Linda
    normalizes these harmless variations instead of retrying the whole job.
    """
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() in {"```json", "```"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    for candidate in (raw,):
        try:
            return json.loads(candidate, strict=False)
        except Exception:
            pass
    # Extract the first JSON object/array from explanatory prose.
    starts = [x for x in (raw.find("{"), raw.find("[")) if x >= 0]
    if starts:
        start = min(starts)
        decoder = json.JSONDecoder(strict=False)
        try:
            parsed, _ = decoder.raw_decode(raw[start:])
            return parsed
        except Exception:
            pass
    return value


def as_json_object(value: Any) -> dict[str, Any]:
    """Return the first useful JSON object from Router/provider wrappers.

    Router 2.x and providers may wrap structured output as e.g.
    {"type":"json","value":{...}}, {"result":"{...}"}, or a one-item
    list. Keep this tolerant so a harmless response envelope does not turn into
    three queue retries and a failed publication.
    """
    wrapper_keys = ("value", "json", "data", "result", "output", "payload", "content", "response")

    def walk(node: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 6:
            return None
        parsed = _decode_json_loose(node)
        if isinstance(parsed, dict):
            # Prefer nested structured payloads before returning a transport envelope.
            for key in wrapper_keys:
                if key in parsed:
                    found = walk(parsed.get(key), depth + 1)
                    if found:
                        return found
            transport_only = set(parsed).issubset({"type", "status", "validated", "result_type", *wrapper_keys})
            if parsed and not transport_only:
                return parsed
            if parsed:
                return parsed
        if isinstance(parsed, list):
            dicts: list[dict[str, Any]] = []
            for item in parsed:
                found = walk(item, depth + 1)
                if found:
                    dicts.append(found)
            if len(dicts) == 1:
                return dicts[0]
            if dicts:
                merged: dict[str, Any] = {}
                for item in dicts:
                    merged.update(item)
                if merged:
                    return merged
        return None

    found = walk(value)
    if found is not None:
        return found
    raise ValueError("Engine nevrátil očekávaný JSON objekt.")


def as_json_list(value: Any) -> list[Any]:
    parsed = _decode_json_loose(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("posts", "items", "data", "result"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
    raise ValueError("Engine nevrátil očekávané JSON pole.")
