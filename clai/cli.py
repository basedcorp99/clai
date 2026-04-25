from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# Cheapest/fastest OpenAI API default. Codex OAuth may not support this model,
# so the Codex provider keeps using the local Codex default unless --model is set.
DEFAULT_OPENAI_MODEL = "gpt-4.1-nano"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4.1-nano"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_CODEX_REASONING_EFFORT = "none"  # "thinking off" for Codex OAuth.


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    token: str
    model: Optional[str]


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
    openrouter_key = args.openrouter_key or os.environ.get("OPENROUTER_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    codex_token = _codex_token()

    # User asked for local Codex OAuth first, then OpenRouter. Codex OAuth tokens
    # do not necessarily have generic OpenAI API scopes, so use the installed
    # Codex CLI when possible; it knows how to use/refresh its local OAuth auth.
    if not args.provider or args.provider == "auto":
        if codex_token and shutil.which("codex"):
            return Provider("codex", "codex", "", args.model or DEFAULT_CODEX_MODEL)
        if openrouter_key:
            return Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions", openrouter_key, args.model or DEFAULT_OPENROUTER_MODEL)
        if openai_key:
            return Provider("openai", "https://api.openai.com/v1/responses", openai_key, args.model or DEFAULT_OPENAI_MODEL)
    elif args.provider == "openai":
        if openai_key:
            return Provider("openai", "https://api.openai.com/v1/responses", openai_key, args.model or DEFAULT_OPENAI_MODEL)
    elif args.provider == "codex" and codex_token and shutil.which("codex"):
        return Provider("codex", "codex", "", args.model or DEFAULT_CODEX_MODEL)
    elif args.provider == "openrouter" and openrouter_key:
        return Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions", openrouter_key, args.model or DEFAULT_OPENROUTER_MODEL)

    raise SystemExit(
        "No LLM credentials found. Login with Codex so ~/.codex/auth.json exists, "
        "or set OPENROUTER_API_KEY."
    )


def build_prompt(request: str) -> list[dict[str, str]]:
    cwd = os.getcwd()
    shell = os.environ.get("SHELL", "/bin/sh")
    system = (
        "You translate natural language into exactly one shell command. "
        "Return JSON only, no markdown, with keys: command, explanation. "
        "The command must be suitable for the user's current OS/shell. "
        "Do not include comments or multiple alternatives. "
        "If the request is ambiguous, still choose the most likely safe command."
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
            f"model_reasoning_effort=\"{DEFAULT_CODEX_REASONING_EFFORT}\"",
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
        headers.update({"HTTP-Referer": "https://github.com/local/clai", "X-Title": "clai"})
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

    if not command:
        raise RuntimeError(f"LLM did not return a command: {text!r}")
    return command, explanation


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate natural language into a shell command and run it.")
    parser.add_argument("request", nargs="*", help="What you want the shell command to do")
    parser.add_argument("--dry", "-n", action="store_true", help="Only print the command; do not execute it")
    parser.add_argument("--model", help="Model to use (defaults depend on provider)")
    parser.add_argument("--provider", choices=["auto", "codex", "openai", "openrouter"], default="auto")
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

    return subprocess.run(command, shell=True).returncode


if __name__ == "__main__":
    raise SystemExit(main())
