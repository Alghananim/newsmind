# -*- coding: utf-8 -*-
"""openai_brain.py — Single LLM reasoning module for all 5 brains.

Architecture:
- One OpenAIClient (singleton) handles HTTP to OpenAI Chat Completions API
- review_brain_outputs() takes ALL brain outputs + GateMind decision
  and returns an LLMReview with concerns, severity, suggestion
- Default-deny: any failure → severity=unknown, suggestion=defer_to_mechanical
- LLM CAN downgrade (allow→wait, wait→block) but NEVER upgrade
- Logs all critical failures to SmartNoteBook (caller's responsibility)

Fail-safe:
- No API key → LLM_AVAILABLE=False, returns disabled review
- HTTP failure → severity=unknown, suggestion=defer
- Timeout (5s) → same as above
- Bad JSON response → same as above
- Caller must treat 'unknown' severity as MECHANICAL-ONLY decision
"""
from __future__ import annotations
import os
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Any
import urllib.request
import urllib.error

log = logging.getLogger("llm.openai_brain")

# ---- Config ------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
LLM_TIMEOUT_SEC = float(os.environ.get("LLM_TIMEOUT_SEC", "5.0"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "300"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))

LLM_AVAILABLE = bool(OPENAI_API_KEY)
LLM_DISABLED_REASON = "" if LLM_AVAILABLE else "no_OPENAI_API_KEY"


# ---- Data --------------------------------------------------------------
@dataclass
class LLMReview:
    """Second-opinion from LLM on the brains' decision."""
    success: bool = False
    severity: str = "unknown"          # 'low' | 'medium' | 'high' | 'unknown'
    suggestion: str = "defer"          # 'agree' | 'downgrade' | 'block' | 'defer'
    confidence: float = 0.0            # 0..1
    concerns: tuple = ()               # tuple of short strings
    reasoning: str = ""                # human-readable
    model_used: str = ""
    latency_ms: float = 0.0
    cost_estimate_usd: float = 0.0     # rough
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success, "severity": self.severity,
            "suggestion": self.suggestion, "confidence": self.confidence,
            "concerns": list(self.concerns), "reasoning": self.reasoning[:500],
            "model_used": self.model_used, "latency_ms": self.latency_ms,
            "error": self.error,
        }


# ---- Client ------------------------------------------------------------
class OpenAIClient:
    """Minimal stdlib-only OpenAI Chat Completions client."""
    BASE_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str = "", model: str = "", timeout_sec: float = 5.0):
        self.api_key = api_key or OPENAI_API_KEY
        self.model = model or LLM_MODEL
        self.timeout = timeout_sec
        self.calls = 0
        self.failures = 0

    def chat(self, system: str, user: str,
             max_tokens: int = LLM_MAX_TOKENS,
             temperature: float = LLM_TEMPERATURE) -> tuple[Optional[str], str]:
        """Returns (content, error). content=None on failure."""
        if not self.api_key:
            return None, "no_api_key"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.BASE_URL, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                return content, ""
        except urllib.error.HTTPError as e:
            self.failures += 1
            return None, f"http_{e.code}"
        except urllib.error.URLError as e:
            self.failures += 1
            return None, f"url_error:{type(e.reason).__name__}"
        except (TimeoutError, OSError) as e:
            self.failures += 1
            return None, f"timeout_or_socket:{type(e).__name__}"
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            self.failures += 1
            return None, f"bad_response:{type(e).__name__}"
        except Exception as e:
            self.failures += 1
            return None, f"unexpected:{type(e).__name__}"


_CLIENT: Optional[OpenAIClient] = None


def _get_client() -> OpenAIClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAIClient()
    return _CLIENT


# ---- Prompts -----------------------------------------------------------
_SYSTEM_PROMPT = """You are a senior trading analyst reviewing the output of an algorithmic trading system.
The system has 5 brains: NewsMind, MarketMind, ChartMind, GateMind, SmartNoteBook.
Your job: critique their joint decision. You can flag concerns but you ALWAYS bias toward safety.
NEVER suggest taking a trade if there's any doubt.

Respond ONLY in JSON with this exact schema:
{
  "severity": "low" | "medium" | "high",
  "suggestion": "agree" | "downgrade" | "block",
  "confidence": 0.0 to 1.0,
  "concerns": ["short concern 1", "short concern 2"],
  "reasoning": "1-2 sentences max"
}

Rules:
- "agree": brains' decision is sound
- "downgrade": valid concern but not blocking (e.g., reduce position or wait)
- "block": material issue that REQUIRES blocking the trade
- High severity → must use "block" or "downgrade"
- Be conservative: when in doubt, downgrade
"""


def _build_user_prompt(brain_outputs: dict, gate_decision: dict) -> str:
    """Format the brain outputs as a user message."""
    return f"""Review this trading decision for EUR/USD.

NewsMind: grade={brain_outputs.get('news_grade','?')} perm={brain_outputs.get('news_perm','?')} bias={brain_outputs.get('news_bias','?')} reason={brain_outputs.get('news_reason','')[:160]}

MarketMind: grade={brain_outputs.get('market_grade','?')} perm={brain_outputs.get('market_perm','?')} regime={brain_outputs.get('market_regime','?')} direction={brain_outputs.get('market_direction','?')} reason={brain_outputs.get('market_reason','')[:160]}

ChartMind: grade={brain_outputs.get('chart_grade','?')} perm={brain_outputs.get('chart_perm','?')} structure={brain_outputs.get('chart_structure','?')} entry={brain_outputs.get('chart_entry_quality','?')} rr={brain_outputs.get('chart_rr','?')} reason={brain_outputs.get('chart_reason','')[:160]}

GateMind decision: {gate_decision.get('final_decision','?')} reason={gate_decision.get('reason','')[:200]}

Critique this. Are there contradictions? Is the decision safe?"""


# ---- Public API --------------------------------------------------------
def review_brain_outputs(brain_outputs: dict,
                         gate_decision: dict) -> LLMReview:
    """Get a second-opinion review from the LLM.

    brain_outputs: dict with news_*, market_*, chart_* keys (from MindOutputs)
    gate_decision: dict with final_decision, reason keys (from GateDecision)

    Returns LLMReview with severity/suggestion. ALWAYS returns a valid review
    (success=False if LLM unavailable or failed — caller can ignore).
    """
    if not LLM_AVAILABLE:
        return LLMReview(error=LLM_DISABLED_REASON or "llm_unavailable")

    client = _get_client()
    user_prompt = _build_user_prompt(brain_outputs, gate_decision)

    t0 = time.time()
    content, err = client.chat(_SYSTEM_PROMPT, user_prompt)
    latency_ms = (time.time() - t0) * 1000

    if err:
        return LLMReview(error=err, model_used=client.model, latency_ms=latency_ms)

    # Parse JSON response
    try:
        parsed = json.loads(content) if content else {}
    except (json.JSONDecodeError, TypeError):
        return LLMReview(error="bad_json", model_used=client.model,
                         latency_ms=latency_ms,
                         reasoning=(content or "")[:200])

    severity = str(parsed.get("severity", "unknown")).lower()
    if severity not in ("low", "medium", "high"):
        severity = "unknown"
    suggestion = str(parsed.get("suggestion", "defer")).lower()
    if suggestion not in ("agree", "downgrade", "block", "defer"):
        suggestion = "defer"

    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    concerns = parsed.get("concerns", [])
    if not isinstance(concerns, (list, tuple)):
        concerns = [str(concerns)]
    concerns = tuple(str(c)[:120] for c in concerns[:5])

    reasoning = str(parsed.get("reasoning", ""))[:500]

    return LLMReview(
        success=True,
        severity=severity,
        suggestion=suggestion,
        confidence=confidence,
        concerns=concerns,
        reasoning=reasoning,
        model_used=client.model,
        latency_ms=latency_ms,
    )


def health_check() -> dict:
    """Quick LLM connectivity test. Returns dict with status."""
    if not LLM_AVAILABLE:
        return {"available": False, "reason": LLM_DISABLED_REASON}
    client = _get_client()
    content, err = client.chat(
        "You respond ONLY in JSON.",
        'Say {"status":"ok"}',
        max_tokens=20,
    )
    if err or not content:
        return {"available": False, "reason": err, "calls": client.calls}
    return {
        "available": True, "model": client.model,
        "calls": client.calls, "failures": client.failures,
        "sample_response": content[:80],
    }
