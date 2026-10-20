import json
import re
import time
import logging
import httpx
import openai
from pydantic import BaseModel, ValidationError
from typing import Type, TypeVar, Optional
from app.config import settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Rough characters-per-token ratio for code-heavy prompts; used only for budgeting.
CHARS_PER_TOKEN = 3

# ---------------------------------------------------------------------------
# Settings helpers (tests replace `settings` with a MagicMock, whose missing
# attributes are MagicMocks rather than defaults)
# ---------------------------------------------------------------------------

def _setting(name: str, default, kind: type):
    value = getattr(settings, name, default)
    if kind is int and isinstance(value, bool):
        return default
    return value if isinstance(value, kind) else default

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class LLMOutputError(ValueError):
    """The model answered, but the answer is not valid for the requested schema."""
    def __init__(self, message: str, raw_output: str = ""):
        super().__init__(message)
        self.raw_output = raw_output

class LLMUnavailableError(RuntimeError):
    """The provider could not be reached or did not answer in time."""

class LLMTimeoutError(LLMUnavailableError, TimeoutError):
    pass

class LLMRequestError(RuntimeError):
    """The provider rejected the request (bad model name, auth, oversized prompt); retrying cannot help."""

_QUOTA_KEYWORDS = ("quota", "rate limit", "rate_limit", "resource_exhausted", "resourceexhausted", "too many requests")

def _is_quota_error(exc: Exception) -> bool:
    """Return True only for quota-exhaustion / rate-limit errors."""
    if isinstance(exc, openai.RateLimitError):
        return True

    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)

    if status == 429:
        return True

    msg_lower = str(exc).lower()

    # HTTP 403 is quota only when the body says so (Gemini RESOURCE_EXHAUSTED)
    if status == 403:
        return any(kw in msg_lower for kw in _QUOTA_KEYWORDS)

    # Catch provider SDKs that surface quota info in the message
    if any(kw in msg_lower for kw in _QUOTA_KEYWORDS):
        if status in (401, 403):
            return False
        return True

    return False

def _is_transient_error(exc: Exception) -> bool:
    """Return True for transient server errors worth retrying."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (500, 502, 503, 504):
        return True
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, LLMUnavailableError)):
        return True
    return False

def _is_non_retryable(exc: Exception) -> bool:
    if isinstance(exc, LLMRequestError):
        return True
    return isinstance(exc, (
        openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError, openai.BadRequestError,
    )) and not _is_quota_error(exc)

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|authorization|bearer)[\"'=:\s]+[A-Za-z0-9_\-\.]{8,}"), r"\1=***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"), "sk-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "AIza***"),
    (re.compile(r"key=[A-Za-z0-9_\-]{10,}"), "key=***"),
]

def _sanitize(message: str, limit: int = 600) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        message = pattern.sub(repl, message)
    return message if len(message) <= limit else message[:limit] + "...(truncated)"

# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)

def _extract_json_text(raw: str) -> str:
    """Strip markdown fences and surrounding prose around a JSON object."""
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    if text.startswith("{"):
        return text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text

def parse_structured_output(raw: str, response_model: Type[T], schema: dict | None = None) -> T:
    """Validate raw model text against response_model, raising LLMOutputError with a precise reason."""
    if not raw or not raw.strip():
        raise LLMOutputError("Model returned empty response content.", raw)
    text = _extract_json_text(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"Response is not valid JSON ({e.msg} at position {e.pos}).", raw) from e
    if not isinstance(data, dict):
        raise LLMOutputError("Response must be a JSON object.", raw)

    schema = schema or response_model.model_json_schema()
    required = schema.get("required", [])
    if "properties" in data and "type" in data and any(f not in data for f in required):
        raise LLMOutputError(
            "Model returned the JSON schema definition instead of populated data. "
            f"Missing required fields: {[f for f in required if f not in data]}",
            raw,
        )
    try:
        return response_model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()[:10]
        )
        raise LLMOutputError(f"Response failed Pydantic validation: {problems}", raw) from exc

# ---------------------------------------------------------------------------
# Ollama availability check (called once, not per inference)
# ---------------------------------------------------------------------------

def verify_ollama_available(base_url: str = None, model: str = None) -> dict:
    """Check whether Ollama is running and the specified model is available.

    Returns:
        dict: {"available": bool, "message": str, "command": Optional[str]}
    """
    raw_base = base_url or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    base = str(raw_base).rstrip("/")
    mdl = model or getattr(settings, "OLLAMA_MODEL", "qwen2.5-coder:7b")

    try:
        resp = httpx.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        models_list = resp.json().get("models", [])
        available_names = [m.get("name", "") for m in models_list]

        # Exact match or prefix match (e.g. qwen2.5-coder:7b vs qwen2.5-coder:7b-instruct)
        matched = False
        target_base = mdl.split(":")[0]
        for name in available_names:
            if name == mdl or name == f"{mdl}:latest":
                matched = True
                break
            # Match base if tag specified
            if ":" in mdl and name.startswith(mdl):
                matched = True
                break
            if name.split(":")[0] == target_base and ":" not in mdl:
                matched = True
                break

        if matched:
            return {
                "available": True,
                "message": f"Ollama is reachable and model '{mdl}' is available.",
                "command": None,
            }

        return {
            "available": False,
            "message": (
                f"Ollama is running at {base}, but model '{mdl}' was not found. "
                f"Available models: {available_names}. "
                f"Run: ollama pull {mdl}"
            ),
            "command": f"ollama pull {mdl}",
        }
    except httpx.ConnectError:
        return {
            "available": False,
            "message": (
                f"Cannot connect to Ollama at {base}. Is Ollama running? "
                "Start it with: ollama serve"
            ),
            "command": "ollama serve",
        }
    except Exception as e:
        return {
            "available": False,
            "message": f"Ollama health check failed: {e}",
            "command": f"ollama pull {mdl}",
        }

# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------

PAID_PROVIDERS = {"openai", "gemini"}

class LLMService:
    def __init__(self):
        raw_provider = getattr(settings, "LLM_PROVIDER", "ollama")
        self.provider = str(raw_provider).lower() if isinstance(raw_provider, str) else "ollama"

        raw_fallback = getattr(settings, "LLM_FALLBACK_PROVIDER", None)
        if isinstance(raw_fallback, str) and raw_fallback.strip():
            self.fallback_provider = raw_fallback.strip().lower()
        else:
            self.fallback_provider = None

        self.max_retries = _setting("MAX_LLM_RETRIES", 2, int)
        self.allow_paid_fallback = _setting("LLM_ALLOW_PAID_FALLBACK", False, bool)
        self.fallback_on = _setting("LLM_FALLBACK_ON", "quota", str).lower()

        # Build primary config
        self.primary = self._build_provider_config(self.provider)

        # Build fallback config (if set and different from primary)
        self.fallback = None
        if self.fallback_provider and self.fallback_provider != self.provider:
            self.fallback = self._build_provider_config(self.fallback_provider)

        # Backward compatibility: expose flat attributes used by existing tests
        self.api_key = self.primary["api_key"]
        self.model = self.primary["model"]
        self.base_url = self.primary["base_url"]

        logger.info(
            "LLMService initialized [Provider: %s, Model: %s, Fallback: %s]",
            self.provider,
            self.model,
            self.fallback_provider,
        )

    # ---- provider config builder ----

    @staticmethod
    def _build_provider_config(provider_name: str) -> dict:
        if provider_name == "ollama":
            raw_base = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
            base = str(raw_base).rstrip("/")
            model_name = getattr(settings, "OLLAMA_MODEL", "qwen2.5-coder:7b")
            return {
                "name": "ollama",
                "api_key": "ollama",  # dummy key for compatibility
                "model": model_name,
                "base_url": f"{base}/v1",
                "ollama_base": base,
                "is_ollama": True,
            }
        elif provider_name == "openai":
            return {
                "name": "openai",
                "api_key": settings.OPENAI_API_KEY,
                "model": getattr(settings, "OPENAI_MODEL", "gpt-4o"),
                "base_url": None,
                "is_ollama": False,
            }
        elif provider_name == "gemini":
            return {
                "name": "gemini",
                "api_key": settings.GEMINI_API_KEY,
                "model": getattr(settings, "GEMINI_MODEL", "gemini-1.5-pro"),
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "is_ollama": False,
            }
        else:
            raise NotImplementedError(
                f"LLM Provider '{provider_name}' not supported. Set LLM_PROVIDER to one of: ollama, gemini, openai."
            )

    # ---- prompt budgeting ----

    def prompt_budget_chars(self) -> int:
        """Approximate number of prompt characters the primary provider can accept.

        Agents use this to trim repository context *before* calling the model,
        instead of letting the provider silently cut off the start of the prompt.
        """
        if self.primary["is_ollama"]:
            num_ctx = _setting("OLLAMA_NUM_CTX", 16384, int)
            num_predict = _setting("OLLAMA_NUM_PREDICT", 4096, int)
            return max((num_ctx - num_predict - 512) * CHARS_PER_TOKEN, 4000)
        return max(_setting("MAX_CONTEXT_TOKENS", 12000, int) * CHARS_PER_TOKEN, 8000)

    # ---- single-provider call ----

    def _call_provider(self, cfg: dict, system_prompt: str, user_prompt: str, response_model: Type[T]) -> T:
        """Call one provider with bounded retries.

        - invalid output: retried with the validation error fed back to the model
        - transient/server errors: retried with backoff (timeouts at most once)
        - quota errors: raised immediately so the caller can decide on fallback
        - configuration/request errors: raised immediately, retrying cannot help
        """
        # API key validation (skip for Ollama)
        if not cfg["is_ollama"] and not cfg["api_key"]:
            raise ValueError(
                f"Configuration Error: {cfg['name'].upper()}_API_KEY not configured. "
                "Cannot process real LLM requests."
            )

        client = None
        if not cfg["is_ollama"]:
            if cfg.get("base_url"):
                client = openai.OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
            else:
                client = openai.OpenAI(api_key=cfg["api_key"])

        feedback: list[dict] = []
        timeouts = 0
        last_error: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                if cfg["is_ollama"]:
                    result = self._call_ollama(client, cfg, system_prompt, user_prompt, response_model, feedback)
                else:
                    result = self._call_cloud(client, cfg, system_prompt, user_prompt, response_model, feedback)
                logger.info(
                    "LLM call ok [provider=%s model=%s schema=%s attempt=%d/%d duration=%.1fs]",
                    cfg["name"], cfg["model"], response_model.__name__, attempt, attempts, time.monotonic() - started,
                )
                return result
            except Exception as e:
                last_error = e
                logger.warning(
                    "LLM call failed [provider=%s model=%s schema=%s attempt=%d/%d duration=%.1fs]: %s: %s",
                    cfg["name"], cfg["model"], response_model.__name__, attempt, attempts,
                    time.monotonic() - started, type(e).__name__, _sanitize(str(e)),
                )
                if _is_quota_error(e) or _is_non_retryable(e):
                    raise
                if isinstance(e, (LLMTimeoutError, openai.APITimeoutError)):
                    timeouts += 1
                    if timeouts > 1:
                        break
                if attempt == attempts:
                    break
                if isinstance(e, LLMOutputError):
                    # Same prompt at temperature 0 would produce the same answer;
                    # show the model what was wrong instead.
                    feedback = [
                        {"role": "assistant", "content": (e.raw_output or "")[:4000]},
                        {"role": "user", "content": (
                            f"Your previous response was rejected: {e}\n"
                            "Return ONLY a corrected JSON object that satisfies the schema, with every required field populated."
                        )},
                    ]
                else:
                    time.sleep(min(2 ** (attempt - 1), 8))

        raise RuntimeError(
            f"[{cfg['name']}] maximum retry bounds exceeded after {attempt} attempt(s): {_sanitize(str(last_error))}"
        ) from last_error

    def _call_cloud(self, client, cfg: dict, system_prompt: str, user_prompt: str, response_model: Type[T], feedback=None) -> T:
        """Cloud provider call using beta structured output parsing."""
        try:
            response = client.beta.chat.completions.parse(
                model=cfg["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    *(feedback or []),
                ],
                response_format=response_model,
            )
        except (ValidationError, openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as e:
            raise LLMOutputError(f"Structured output rejected: {e}") from e
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise LLMOutputError("Parsed structured output returned Null.")
        return parsed

    def _call_ollama(
        self,
        client,
        cfg: dict,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        feedback=None,
    ) -> T:
        """Call Ollama with native schema-constrained structured output via /api/chat."""
        schema = response_model.model_json_schema()
        raw_base = cfg.get("ollama_base") or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        base_url = str(raw_base).rstrip("/")
        timeout = _setting("OLLAMA_TIMEOUT", 600, int)
        num_ctx = _setting("OLLAMA_NUM_CTX", 16384, int)
        num_predict = _setting("OLLAMA_NUM_PREDICT", 4096, int)

        # Clear instructions to populate fields rather than echoing the schema
        augmented_system = (
            f"{system_prompt}\n\n"
            "INSTRUCTIONS: You must populate and return a single valid JSON object with actual values "
            "satisfying all required fields. "
            "Do NOT return the schema, field descriptions, or schema definitions. "
            "Do NOT output markdown commentary or text outside the JSON object."
        )
        messages = [
            {"role": "system", "content": augmented_system},
            {"role": "user", "content": user_prompt},
            *(feedback or []),
        ]

        # Ollama drops the *start* of an over-long prompt (the system instructions)
        # without any error, which is what produced schema-less output before.
        est_tokens = (sum(len(m["content"]) for m in messages) + len(json.dumps(schema))) // CHARS_PER_TOKEN
        if est_tokens + num_predict > num_ctx:
            raise LLMRequestError(
                f"Prompt too large for Ollama context window: ~{est_tokens} prompt tokens + {num_predict} output "
                f"tokens exceeds OLLAMA_NUM_CTX={num_ctx}. Increase OLLAMA_NUM_CTX or reduce the context."
            )

        payload = {
            "model": cfg["model"],
            "messages": messages,
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
        }

        endpoint = f"{base_url}/api/chat"
        try:
            response = httpx.post(endpoint, json=payload, timeout=timeout)
            response.raise_for_status()
        except httpx.ConnectError as ce:
            raise LLMUnavailableError(
                f"Cannot connect to Ollama server at {base_url}. "
                "Ensure Ollama is running on the host (e.g. run 'ollama serve')."
            ) from ce
        except httpx.TimeoutException as te:
            raise LLMTimeoutError(
                f"Ollama request timed out after {timeout} seconds. Increase OLLAMA_TIMEOUT or use a faster model."
            ) from te
        except httpx.HTTPStatusError as hse:
            body = hse.response.text[:300]
            if hse.response.status_code == 404 or "not found" in body.lower():
                raise LLMRequestError(
                    f"Ollama model '{cfg['model']}' not found on server. "
                    f"Please run: ollama pull {cfg['model']}"
                ) from hse
            if hse.response.status_code >= 500:
                raise LLMUnavailableError(f"Ollama server error {hse.response.status_code}: {body}") from hse
            raise LLMRequestError(f"Ollama rejected the request ({hse.response.status_code}): {body}") from hse

        data = response.json()
        raw = (data.get("message") or {}).get("content", "").strip()

        prompt_tokens = data.get("prompt_eval_count")
        if isinstance(prompt_tokens, int) and prompt_tokens >= num_ctx - 8:
            logger.warning("Ollama prompt filled the whole context window (%d tokens); input may have been truncated.", prompt_tokens)
        if data.get("done_reason") == "length":
            # The tail shows whether the model looped (repeated text/whitespace) or was writing a huge edit.
            logger.warning("Ollama output hit the token limit; last characters: %r", raw[-400:])
            raise LLMOutputError(
                f"Ollama response was cut off at OLLAMA_NUM_PREDICT={num_predict} tokens before the JSON was complete.",
                raw,
            )

        try:
            return parse_structured_output(raw, response_model, schema)
        except LLMOutputError as exc:
            logger.error("Ollama response failed Pydantic validation: %s\nRaw output: %s", exc, raw[:500])
            raise LLMOutputError(f"Ollama response failed Pydantic validation: {exc}", raw) from exc

    # ---- public interface (supports 2-arg and 3-arg invocations) ----

    def _fallback_allowed(self, error: Exception) -> bool:
        if not self.fallback:
            return False
        # _call_provider wraps exhausted retries in RuntimeError; judge by the underlying error.
        cause = error.__cause__ if error.__cause__ is not None else error
        unavailable = _is_transient_error(cause)
        if not (_is_quota_error(error) or _is_quota_error(cause) or (self.fallback_on == "unavailable" and unavailable)):
            return False
        if self.fallback["name"] in PAID_PROVIDERS and not self.allow_paid_fallback:
            logger.error(
                "Not falling back from %s to %s: the fallback is a paid API and LLM_ALLOW_PAID_FALLBACK is false.",
                self.primary["name"], self.fallback["name"],
            )
            return False
        return True

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str = None,
        response_model: Type[T] = None,
    ) -> T:
        """Generate a validated structured response, or raise. Never returns placeholder data."""
        # Normalize 2-argument calls: generate_structured(prompt, ResponseModel)
        if response_model is None and isinstance(user_prompt, type) and issubclass(user_prompt, BaseModel):
            response_model = user_prompt
            user_prompt = system_prompt
            system_prompt = "You are an AI assistant that produces structured JSON conforming strictly to the requested schema."

        if response_model is None:
            raise ValueError("response_model must be provided as a Pydantic BaseModel class.")

        primary_name = self.primary["name"]

        try:
            return self._call_provider(self.primary, system_prompt, user_prompt, response_model)
        except Exception as primary_err:
            if not self._fallback_allowed(primary_err):
                if _is_quota_error(primary_err) and not self.fallback:
                    logger.error(f"[{primary_name}] Quota exhausted and no fallback provider configured.")
                raise
            fb_name = self.fallback["name"]
            logger.warning(
                "[%s] failed (%s); falling back to %s.", primary_name, _sanitize(str(primary_err), 200), fb_name
            )
            try:
                result = self._call_provider(self.fallback, system_prompt, user_prompt, response_model)
                logger.info(f"Fallback to {fb_name} succeeded.")
                return result
            except Exception as fb_err:
                raise RuntimeError(
                    f"Both providers failed. "
                    f"Primary ({primary_name}): {_sanitize(str(primary_err), 300)}. "
                    f"Fallback ({fb_name}): {_sanitize(str(fb_err), 300)}"
                ) from fb_err
