"""DeepSeek chat client: JSON mode, schema validation with retry, on-disk cache, usage log."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import jsonschema
import requests

from .common import ROOT

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"


def api_key() -> str:
    """DEEPSEEK_API_KEY from the environment, else from this repo's .env (see .env.example)."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"].strip()
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "DEEPSEEK_API_KEY" and value.strip():
                return value.strip().strip("\"'")
    raise RuntimeError("No DeepSeek key: set DEEPSEEK_API_KEY in the environment or in .env (see .env.example)")


class Truncated(Exception):
    pass


class SchemaFailure(Exception):
    def __init__(self, errors, content):
        super().__init__("; ".join(errors[:3]))
        self.errors, self.content = errors, content


class LLM:
    def __init__(self, cache_dir: Path, model: str = DEFAULT_MODEL, effort: str = "high",
                 max_tokens: int = 32000):
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.usage_log = self.cache_dir.parent / "usage.jsonl"
        self._key = None

    def _post(self, messages: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0,
                "effort": self.effort, "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"}}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / f"{h}.json"
        if path.exists():
            return json.loads(path.read_text())
        self._key = self._key or api_key()
        for attempt in range(5):
            try:
                r = requests.post(API_URL, json=body, timeout=(10, 600),
                                  headers={"Authorization": f"Bearer {self._key}"})
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            resp = r.json()
            path.write_text(json.dumps(resp))
            with self.usage_log.open("a") as f:
                f.write(json.dumps({"t": time.time(), "model": self.model, "usage": resp.get("usage")}) + "\n")
            return resp
        raise RuntimeError("DeepSeek request failed after 5 attempts")

    def json_call(self, messages: list[dict], schema: dict | None = None,
                  check=None, max_retries: int = 2) -> dict:
        """Returns parsed JSON. `check(obj)` may return extra error strings.
        On validation failure the errors are sent back and the model retries."""
        msgs = list(messages)
        for attempt in range(max_retries + 1):
            resp = self._post(msgs)
            choice = resp["choices"][0]
            content = choice["message"].get("content") or ""
            if choice.get("finish_reason") == "length":
                raise Truncated()
            try:
                obj = json.loads(content)
                errors = []
                if schema is not None:
                    v = jsonschema.Draft202012Validator(schema)
                    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                              for e in v.iter_errors(obj)]
                if check and not errors:
                    errors = list(check(obj))
            except json.JSONDecodeError as e:
                obj, errors = None, [f"invalid JSON: {e}"]
            if not errors:
                return obj
            if attempt == max_retries:
                raise SchemaFailure(errors, content)
            msgs = msgs + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Your JSON does not match the schema:\n- "
                 + "\n- ".join(errors[:15]) + "\nReturn the complete corrected JSON object."},
            ]
