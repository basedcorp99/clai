from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_MODEL = DEFAULT_MODEL
DEFAULT_OPENROUTER_MODEL = f"openai/{DEFAULT_MODEL}"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "none"


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    token: str
    model: Optional[str]
    reasoning_effort: Optional[str] = None


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _codex_auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"


def _config_paths() -> list[Path]:
    paths: list[Path] = []
    if os.environ.get("CLAI_CONFIG"):
        paths.append(Path(os.environ["CLAI_CONFIG"]))
    paths.extend([
        Path.cwd() / ".clai.json",
        Path.home() / ".clai.json",
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "clai" / "config.json",
    ])
    return paths


def load_config() -> dict[str, Any]:
    for path in _config_paths():
        data = _load_json(path)
        if data:
            return data
    return {}


def _config_str(config: dict[str, Any], key: str) -> Optional[str]:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _model_for(config: dict[str, Any], provider: str, fallback: str) -> str:
    return _config_str(config, f"{provider}_model") or _config_str(config, "model") or fallback


def _codex_token() -> Optional[str]:
    auth = _load_json(_codex_auth_path())

    # Codex auth.json commonly stores ChatGPT OAuth here. Do not log this value.
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str):
        return tokens["access_token"]

    # Some installs may have a plain API key in the same file.
    key = auth.get("OPENAI_API_KEY")
    if isinstance(key, str) and key.strip():
        return key.strip()

    return None


def resolve_provider(args: argparse.Namespace) -> Provider:
    config = load_config()
    provider_name = args.provider or _config_str(config, "provider") or "auto"
    openrouter_key = args.openrouter_key or os.environ.get("OPENROUTER_API_KEY") or _config_str(config, "openrouter_api_key")
    openai_key = os.environ.get("OPENAI_API_KEY") or _config_str(config, "openai_api_key")
    codex_token = _codex_token()
    codex_reasoning = _config_str(config, "codex_reasoning_effort") or DEFAULT_CODEX_REASONING_EFFORT

    # User asked for local Codex OAuth first, then OpenRouter. Codex OAuth tokens
    # do not necessarily have generic OpenAI API scopes, so use the installed
    # Codex CLI when possible; it knows how to use/refresh its local OAuth auth.
    if provider_name == "auto":
        if codex_token and shutil.which("codex"):
            return Provider("codex", "codex", "", args.model or _model_for(config, "codex", DEFAULT_CODEX_MODEL), codex_reasoning)
        if openrouter_key:
            return Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions", openrouter_key, args.model or _model_for(config, "openrouter", DEFAULT_OPENROUTER_MODEL))
        if openai_key:
            return Provider("openai", "https://api.openai.com/v1/responses", openai_key, args.model or _model_for(config, "openai", DEFAULT_OPENAI_MODEL))
    elif provider_name == "openai":
        if openai_key:
            return Provider("openai", "https://api.openai.com/v1/responses", openai_key, args.model or _model_for(config, "openai", DEFAULT_OPENAI_MODEL))
    elif provider_name == "codex" and codex_token and shutil.which("codex"):
        return Provider("codex", "codex", "", args.model or _model_for(config, "codex", DEFAULT_CODEX_MODEL), codex_reasoning)
    elif provider_name == "openrouter" and openrouter_key:
        return Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions", openrouter_key, args.model or _model_for(config, "openrouter", DEFAULT_OPENROUTER_MODEL))

    raise SystemExit(
        "No LLM credentials found. Login with Codex so ~/.codex/auth.json exists, "
        "or set OPENROUTER_API_KEY / OPENAI_API_KEY, or put keys in ~/.clai.json."
    )


def build_prompt(request: str) -> list[dict[str, str]]:
    cwd = os.getcwd()
    shell = os.environ.get("SHELL", "/bin/sh")
    system = (
        "Convert the user's request into one robust shell command. "
        "Return JSON only, no markdown, with keys: command, explanation. "
        "The command must fit the user's OS, shell, and current directory, and be a single line. "
        "The command will already run in the current directory; do not include a cd to it. "
        "Prefer correctness over brevity. "
        "For destructive commands over multiple targets, use canonical machine-readable sources, "
        "filter exact leaf targets before acting, skip empty/meta/container names, quote targets, "
        "avoid xargs, and use explicit loops/checks. "
        "If the active/current item might be deleted, first switch to an existing kept safe item; fail only if none exists. "
        "Do not include comments, markdown, or multiple alternatives."
    )
    user = (
        f"Current working directory: {cwd}\n"
        f"OS: {platform.platform()}\n"
        f"Shell: {shell}\n"
        f"User request: {request}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_codex_model(stdout: str, provider: Provider) -> str:
    if provider.model:
        return provider.model
    for line in stdout.splitlines():
        if line.startswith("model:"):
            return line.split(":", 1)[1].strip()
    return "codex-default"


def call_codex_cli(provider: Provider, request: str) -> LLMResult:
    schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["command", "explanation"],
        "additionalProperties": False,
    }
    messages = build_prompt(request)
    prompt = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    prompt += "\n\nImportant: do not execute any command. Only return the JSON object."

    with tempfile.TemporaryDirectory(prefix="clai-") as td:
        schema_path = Path(td) / "schema.json"
        out_path = Path(td) / "out.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")

        cmd = [
            "codex",
            "--ask-for-approval",
            "never",
            "-c",
            f"model_reasoning_effort=\"{provider.reasoning_effort or DEFAULT_CODEX_REASONING_EFFORT}\"",
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(out_path),
        ]
        if provider.model:
            cmd.extend(["--model", provider.model])
        cmd.append(prompt)

        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"Codex CLI failed ({proc.returncode}): {err}")
        try:
            return LLMResult(out_path.read_text(encoding="utf-8"), _parse_codex_model(proc.stdout + "\n" + proc.stderr, provider))
        except OSError as e:
            raise RuntimeError(f"Codex CLI did not write an output message: {e}") from e


def http_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM request failed ({e.code}): {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM request failed: {e.reason}") from e


def call_llm(provider: Provider, request: str) -> LLMResult:
    if provider.name == "codex":
        return call_codex_cli(provider, request)

    messages = build_prompt(request)
    headers = {"Authorization": f"Bearer {provider.token}"}

    if provider.name == "openrouter":
        headers.update({"X-Title": "clai"})
        data = http_json(provider.base_url, headers, {"model": provider.model, "messages": messages, "temperature": 0})
        return LLMResult(data["choices"][0]["message"]["content"], provider.model or DEFAULT_OPENROUTER_MODEL)

    # OpenAI Responses API. Works with normal API keys and Codex OAuth access tokens.
    data = http_json(
        provider.base_url,
        headers,
        {
            "model": provider.model,
            "input": messages,
            "text": {"format": {"type": "json_object"}},
        },
    )
    if isinstance(data.get("output_text"), str):
        return LLMResult(data["output_text"], provider.model or DEFAULT_OPENAI_MODEL)

    # Conservative fallback for Responses payloads.
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return LLMResult("".join(chunks), provider.model or DEFAULT_OPENAI_MODEL)
    raise RuntimeError(f"Could not read LLM response: {json.dumps(data)[:1000]}")


def extract_command(text: str) -> tuple[str, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        command = str(data.get("command", "")).strip()
        explanation = str(data.get("explanation", "")).strip()
    except json.JSONDecodeError:
        command = raw.splitlines()[0].strip()
        explanation = ""

    command = re.sub(r"\s*\n\s*", " ", command).strip()
    if not command:
        raise RuntimeError(f"LLM did not return a command: {text!r}")
    return command, explanation


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate natural language into a shell command and run it.")
    parser.add_argument("request", nargs="*", help="What you want the shell command to do")
    parser.add_argument("--dry", "-n", action="store_true", help="Only print the command; do not execute it")
    parser.add_argument("--model", help="Model to use (defaults depend on provider)")
    parser.add_argument("--provider", choices=["auto", "codex", "openai", "openrouter"], help="Provider to use (default: config provider or auto)")
    parser.add_argument("--openrouter-key", help="OpenRouter API key (or set OPENROUTER_API_KEY)")
    parser.add_argument("--explain", action="store_true", help="Also print the model's brief explanation")
    if not argv:
        parser.print_help()
        raise SystemExit(0)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    request = " ".join(args.request).strip()
    if not request:
        parse_args([])
        return 0

    try:
        provider = resolve_provider(args)
        result = call_llm(provider, request)
        command, explanation = extract_command(result.text)
    except Exception as e:
        print(f"clai: {e}", file=sys.stderr)
        return 1

    if args.explain and explanation:
        print(f"# {explanation}")
    print(command)

    if args.dry:
        return 0

    shell = os.environ.get("SHELL") or "/bin/sh"
    return subprocess.run(command, shell=True, executable=shell).returncode


if __name__ == "__main__":
    raise SystemExit(main())
