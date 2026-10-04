"""Pato Partner API integration service (v2 HMAC).

Handles:
- Dynamic HMAC-SHA256 signature generation per Pato specification.
- Cryptographic verification of server responses.
- Automated fetching of digital login links upon successful payments.
- One-hour automated warranty link renewal.
- Quota and key expiration checking for admin monitoring.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple
import httpx

from config import PATO_API_KEY, PATO_BASE_URL, PATO_CLIENT_ID

logger = logging.getLogger(__name__)


class PatoAPIError(Exception):
    """Base exception for Pato API errors."""

    def __init__(self, message: str, code: str = "unknown", status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PatoService:
    """Service client for interacting with the Pato Partner API v2."""

    BASE_URL: str = PATO_BASE_URL
    CLIENT_ID: str = PATO_CLIENT_ID
    API_KEY: str = PATO_API_KEY

    @classmethod
    def is_configured(cls) -> bool:
        """Check if Pato credentials are configured."""
        return bool(cls.CLIENT_ID and cls.API_KEY)

    @classmethod
    def _compute_derived_key(cls, api_key: str) -> bytes:
        """Derive 32-byte binary HMAC key from UTF-8 API key via SHA-256."""
        return hashlib.sha256(api_key.encode("utf-8")).digest()

    @classmethod
    def _sign_request(
        cls, method: str, path: str, body_bytes: bytes, api_key: str, client_id: str
    ) -> Tuple[Dict[str, str], bytes, str]:
        """Generate mandatory HMAC-SHA256 headers for Pato v2 API.

        Returns:
            Tuple of (headers_dict, derived_key_bytes, nonce_str)
        """
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        body_hash = hashlib.sha256(body_bytes).hexdigest()

        # Canonical string: timestamp + \n + nonce + \n + METHOD + \n + path_only + \n + body_hash
        canonical = "\n".join((timestamp, nonce, method.upper(), path, body_hash))
        derived_key = cls._compute_derived_key(api_key)
        signature = hmac.new(derived_key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

        headers = {
            "X-Client-ID": client_id,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        }
        if body_bytes:
            headers["Content-Type"] = "application/json"

        return headers, derived_key, nonce

    @classmethod
    def _verify_response(
        cls, response: httpx.Response, derived_key: bytes, request_nonce: str
    ) -> None:
        """Verify server response signature as mandated by Pato security contract."""
        resp_timestamp = response.headers.get("X-Response-Timestamp", "")
        resp_nonce = response.headers.get("X-Response-Nonce", "")
        resp_signature = response.headers.get("X-Response-Signature", "")

        if not resp_signature:
            # 401/403 before authentication may not have a signature
            if response.status_code in (401, 403):
                return
            raise PatoAPIError(
                "Phản hồi từ Pato thiếu chữ ký bảo mật X-Response-Signature.",
                code="missing_response_signature",
                status_code=response.status_code,
            )

        if resp_nonce != request_nonce or not resp_timestamp.isdigit():
            raise PatoAPIError(
                "Phản hồi Pato có nonce hoặc timestamp không khớp với request.",
                code="invalid_response_metadata",
                status_code=response.status_code,
            )

        if abs(int(time.time()) - int(resp_timestamp)) > 300:
            raise PatoAPIError(
                "Chữ ký phản hồi từ Pato đã quá hạn (>300 giây).",
                code="response_signature_expired",
                status_code=response.status_code,
            )

        body_hash = hashlib.sha256(response.content).hexdigest()
        canonical = "\n".join((resp_timestamp, resp_nonce, str(response.status_code), body_hash))
        expected_sig = hmac.new(derived_key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, resp_signature.lower()):
            raise PatoAPIError(
                "Chữ ký phản hồi từ Pato không hợp lệ (Signature mismatch).",
                code="invalid_response_signature",
                status_code=response.status_code,
            )

    @classmethod
    async def request(
        cls,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """Execute a signed request to Pato API with response verification."""
        if not cls.is_configured():
            raise PatoAPIError("Pato API chưa được cấu hình Client ID hoặc API Key.", code="not_configured")

        body_bytes = (
            b""
            if payload is None
            else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )

        headers, derived_key, request_nonce = cls._sign_request(
            method=method,
            path=path,
            body_bytes=body_bytes,
            api_key=cls.API_KEY,
            client_id=cls.CLIENT_ID,
        )

        url = f"{cls.BASE_URL}{path}"
        logger.debug("Pato Request: %s %s (Nonce: %s)", method, url, request_nonce)

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    content=body_bytes,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                logger.error("Pato API network request error on %s: %s", path, exc)
                raise PatoAPIError(f"Lỗi kết nối tới Pato API: {exc}", code="network_error")

        # Verify response signature if present
        cls._verify_response(response, derived_key, request_nonce)

        try:
            data = response.json()
        except Exception:
            data = {"raw_text": response.text}

        if response.status_code not in (200, 202):
            err_info = data.get("error", {}) if isinstance(data, dict) else {}
            err_code = err_info.get("code") or f"http_{response.status_code}"
            err_msg = err_info.get("message") or data.get("message") or f"HTTP {response.status_code}"
            logger.warning("Pato API returned error [%s]: %s (Status: %s)", err_code, err_msg, response.status_code)
            raise PatoAPIError(err_msg, code=err_code, status_code=response.status_code)

        return data

    @classmethod
    async def get_quota(cls) -> Dict[str, Any]:
        """Fetch current remaining quota, delivered count, and key expiration status."""
        return await cls.request("GET", "/api/v1/partner/quota")

    @classmethod
    async def create_order(
        cls,
        request_id: str,
        max_retries: int = 5,
        retry_delay_seconds: float = 4.0,
    ) -> Dict[str, Any]:
        """Request 1 login link from Pato.

        Handles HTTP 202 Processing by retrying with the same request_id
        but generating fresh nonce/timestamp/signature per Pato specification.
        """
        payload = {"request_id": request_id, "quantity": 1}

        for attempt in range(1, max_retries + 1):
            try:
                result = await cls.request("POST", "/api/v1/partner/orders", payload=payload)
                # If 200, completed
                if result.get("success") or "login_link" in result:
                    return result
            except PatoAPIError as exc:
                if exc.status_code == 202:
                    logger.info(
                        "Pato order %s is processing (attempt %d/%d). Retrying in %.1fs...",
                        request_id,
                        attempt,
                        max_retries,
                        retry_delay_seconds,
                    )
                    await asyncio.sleep(retry_delay_seconds)
                    continue
                # For non-202 errors, re-raise
                raise

        raise PatoAPIError(
            f"Đơn hàng Pato ({request_id}) quá thời gian xử lý sau {max_retries} lần thử.",
            code="timeout",
            status_code=504,
        )

    @classmethod
    async def request_warranty(cls, pato_order_id: str, request_id: str) -> Dict[str, Any]:
        """Request a warranty link renewal for an existing order within 1 hour."""
        payload = {
            "order_id": pato_order_id,
            "request_id": request_id,
        }
        return await cls.request("POST", "/api/v1/partner/warranty", payload=payload)

    @classmethod
    async def sync_stock(cls, db=None) -> int:
        """Fetch remaining quota from Pato for monitoring without modifying manual integer stock."""
        try:
            data = await cls.get_quota()
            remaining = int(data.get("remaining_quota", 0))
            logger.debug("Pato partner quota: %d remaining", remaining)
            return remaining
        except Exception as exc:
            logger.warning("Failed to check Pato quota: %s", exc)
            return 0

