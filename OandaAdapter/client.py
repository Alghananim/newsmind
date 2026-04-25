# -*- coding: utf-8 -*-
"""OandaClient — thin, defensive HTTP client over the v20 REST API.

Why a custom client (not the community oandapyV20)
--------------------------------------------------
    1. Pinning surface area. We import six endpoints; bringing in a
       general-purpose wrapper drags in many more behaviours that we
       would have to audit and silence.
    2. Style consistency. Every other I/O-heavy module in the system
       (LLMCore.client, NewsMind adapters) follows the same retry +
       error-translation + cost-tracking pattern. Reusing it here
       keeps the operational surface uniform.
    3. Security posture. We want the credential read to happen at one
       single point we can audit (the constructor reading os.environ),
       with no path that lets a chat-driven caller pass the token in.

Defaults are tuned for live trading
-----------------------------------
    timeout_seconds = 15.0       # broker calls are time-critical
    max_retries     = 2          # 3 attempts total
    backoff_base    = 0.75       # 0.75s, 1.5s, 3s with jitter

429 (rate limit) and 5xx (server) are retried; 4xx other than 429
raise immediately because retrying would only burn budget.

Public surface
--------------
    client = OandaClient()                # reads env at construction
    resp = client.get('/v3/accounts/<id>/instruments/EUR_USD/candles',
                      params={'granularity': 'M15', 'count': 100})
    if resp.ok:
        candles = resp.data['candles']

Every call returns an OandaResponse with `.ok`, `.data`, `.status`,
`.error`, `.latency_seconds`. Callers never have to deal with raw
Response objects.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ----------------------------------------------------------------------
# Errors and config.
# ----------------------------------------------------------------------
class OandaError(RuntimeError):
    """Raised for unrecoverable errors at construction time."""


@dataclass
class OandaConfig:
    """Per-call config. Defaults are production-safe."""
    timeout_seconds: float = 15.0
    max_retries: int = 2
    backoff_base_seconds: float = 0.75


_PRACTICE_BASE = "https://api-fxpractice.oanda.com"
_LIVE_BASE = "https://api-fxtrade.oanda.com"
_VALID_ENVS = ("practice", "live")


# ----------------------------------------------------------------------
# Response container.
# ----------------------------------------------------------------------
@dataclass
class OandaResponse:
    """Outcome of one OANDA API call.

    `ok=True` when status was 2xx and JSON parsed. `data` is the
    parsed JSON body (always a dict for OANDA v20).

    `ok=False` for any other outcome; `error` carries a short
    human-readable string. `status` is the HTTP code (-1 if the
    request never reached the server, e.g. timeout/DNS).
    """
    ok: bool
    data: Optional[dict] = None
    status: int = 0
    error: str = ""
    latency_seconds: float = 0.0
    method: str = ""
    path: str = ""


# ----------------------------------------------------------------------
# The client.
# ----------------------------------------------------------------------
class OandaClient:
    """Thin, defensive client around the v20 REST API.

    Construct once at process start; thread-safe (uses requests.Session
    under the hood, which is thread-safe per request).

    Construction requires environment variables:
        OANDA_API_TOKEN     personal access token
        OANDA_ACCOUNT_ID    account id (e.g. "101-001-12345678-001")
        OANDA_ENVIRONMENT   "practice" (default) or "live"
        OANDA_TIMEOUT_SEC   optional override (float seconds)

    The constructor errors loudly with OandaError if either required
    var is missing — silent disable would hide misconfiguration on a
    24/5 live system.
    """

    def __init__(self,
                 default_cfg: Optional[OandaConfig] = None,
                 ):
        try:
            import requests
        except ImportError as e:
            raise OandaError(
                "requests is required. Add `requests>=2.31` to "
                "requirements.txt and rebuild."
            ) from e
        self._requests = requests

        token = os.environ.get("OANDA_API_TOKEN", "")
        if not token:
            raise OandaError(
                "OANDA_API_TOKEN environment variable is required and "
                "was not set. Configure it via Hostinger Settings -> "
                "Environment Variables (production) or .env (local)."
            )
        account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
        if not account_id:
            raise OandaError(
                "OANDA_ACCOUNT_ID environment variable is required "
                "(format: '101-001-XXXXXXXX-001' for practice, "
                "'001-001-XXXXXXXX-001' for live)."
            )
        env = (os.environ.get("OANDA_ENVIRONMENT", "practice") or "").lower()
        if env not in _VALID_ENVS:
            raise OandaError(
                f"OANDA_ENVIRONMENT must be one of {_VALID_ENVS}; "
                f"got {env!r}"
            )
        try:
            tmo_override = float(os.environ.get("OANDA_TIMEOUT_SEC", ""))
        except ValueError:
            tmo_override = None

        self._token = token
        self._account_id = account_id
        self._env = env
        self._base = _LIVE_BASE if env == "live" else _PRACTICE_BASE

        cfg = default_cfg or OandaConfig()
        if tmo_override is not None:
            cfg = OandaConfig(**{**cfg.__dict__, "timeout_seconds": tmo_override})
        self._cfg = cfg

        self._session = requests.Session()
        # Persistent headers — Authorization + content type. Token is
        # held only in this Session object, never logged.
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Accept-Datetime-Format": "RFC3339",
            "Content-Type": "application/json",
        })

    # ==================================================================
    # Public properties.
    # ==================================================================
    @property
    def account_id(self) -> str:
        """The configured OANDA account id (NOT the token)."""
        return self._account_id

    @property
    def environment(self) -> str:
        """'practice' or 'live'."""
        return self._env

    @property
    def base_url(self) -> str:
        """Base URL the client will hit (no token in URL)."""
        return self._base

    # ==================================================================
    # Public methods.
    # ==================================================================
    def get(self, path: str, *,
            params: Optional[dict] = None,
            cfg: Optional[OandaConfig] = None) -> OandaResponse:
        """GET request to the configured base URL.

        `path` should start with '/'. Example:
            '/v3/accounts/<id>/instruments/EUR_USD/candles'
        """
        return self._call("GET", path, params=params, json_body=None, cfg=cfg)

    def post(self, path: str, *,
             json_body: Optional[dict] = None,
             cfg: Optional[OandaConfig] = None) -> OandaResponse:
        """POST request with JSON body."""
        return self._call("POST", path, params=None, json_body=json_body, cfg=cfg)

    def put(self, path: str, *,
            json_body: Optional[dict] = None,
            cfg: Optional[OandaConfig] = None) -> OandaResponse:
        """PUT request with JSON body. Used to modify trades/orders."""
        return self._call("PUT", path, params=None, json_body=json_body, cfg=cfg)

    # ==================================================================
    # Convenience: build account-scoped paths.
    # ==================================================================
    def account_path(self, suffix: str = "") -> str:
        """Build '/v3/accounts/<account_id><suffix>' for callers."""
        if suffix and not suffix.startswith("/"):
            suffix = "/" + suffix
        return f"/v3/accounts/{self._account_id}{suffix}"

    # ==================================================================
    # Internal: one call with retry + JSON parse + error translation.
    # ==================================================================
    def _call(self, method: str, path: str, *,
              params: Optional[dict],
              json_body: Optional[dict],
              cfg: Optional[OandaConfig]) -> OandaResponse:
        c = cfg or self._cfg
        url = self._base + path
        last_err = ""

        for attempt in range(c.max_retries + 1):
            t0 = time.monotonic()
            try:
                resp = self._session.request(
                    method=method, url=url,
                    params=params, json=json_body,
                    timeout=c.timeout_seconds,
                )
                latency = time.monotonic() - t0
                status = resp.status_code

                # Try to parse JSON; OANDA v20 returns JSON for both
                # success and error bodies.
                body: Optional[dict]
                try:
                    body = resp.json() if resp.text else None
                except json.JSONDecodeError:
                    body = None

                if 200 <= status < 300:
                    return OandaResponse(
                        ok=True, data=body, status=status,
                        latency_seconds=latency,
                        method=method, path=path,
                    )

                # 429 or 5xx → retry
                if (status == 429 or 500 <= status < 600) and attempt < c.max_retries:
                    last_err = self._format_err(status, body)
                    self._backoff(attempt, c)
                    continue

                # Other 4xx → don't retry
                return OandaResponse(
                    ok=False, data=body, status=status,
                    error=self._format_err(status, body),
                    latency_seconds=latency,
                    method=method, path=path,
                )

            except self._requests.exceptions.Timeout as e:
                last_err = f"timeout: {e}"
                if attempt < c.max_retries:
                    self._backoff(attempt, c)
                    continue
            except self._requests.exceptions.ConnectionError as e:
                last_err = f"connection: {e}"
                if attempt < c.max_retries:
                    self._backoff(attempt, c)
                    continue
            except self._requests.exceptions.RequestException as e:
                last_err = f"request: {e}"
                if attempt < c.max_retries:
                    self._backoff(attempt, c)
                    continue

        return OandaResponse(
            ok=False, status=-1, error=last_err,
            method=method, path=path,
        )

    @staticmethod
    def _format_err(status: int, body: Optional[dict]) -> str:
        """Build a short error string from an OANDA error body."""
        if not isinstance(body, dict):
            return f"http {status}"
        msg = body.get("errorMessage") or body.get("message") or ""
        code = body.get("errorCode") or body.get("code") or ""
        return f"http {status}: {code} {msg}".strip()

    def _backoff(self, attempt: int, c: OandaConfig) -> None:
        """Jittered exponential backoff."""
        sleep_for = c.backoff_base_seconds * (2 ** attempt)
        sleep_for *= 0.7 + 0.6 * random.random()
        time.sleep(sleep_for)
