# clai

Command Line Artificial Intelligence: ask for a shell command in plain English, print it, then run it.

```bash
clai "git command to delete branch feature/foo"
# prints and executes something like:
# git branch -d feature/foo
```

## Install

Recommended local install:

```bash
./install.sh
```

The installer follows Python packaging conventions:

1. If `pipx` is installed, it runs `pipx install --force --editable .`.
2. Otherwise, it creates a virtual environment at `~/.local/share/clai-venv`, installs the package editable into that venv, and symlinks `clai` into `~/.local/bin`.

Make sure `~/.local/bin` is on your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Uninstall:

```bash
./uninstall.sh
```

Quick run without installing:

```bash
python3 -m clai --dry "git command to delete branch X"
```

## Configuration and credentials

`clai` reads the first config file it finds:

1. `$CLAI_CONFIG`
2. `.clai.json` in the current directory
3. `~/.clai.json`
4. `~/.config/clai/config.json`

Example:

```bash
cp clai.example.json ~/.clai.json
```

```json
{
  "provider": "auto",
  "model": "gpt-5.4-mini",
  "codex_model": "gpt-5.5",
  "codex_reasoning_effort": "none",
  "openai_model": "gpt-5.4-mini",
  "openai_api_key": "",
  "openrouter_model": "openai/gpt-5.4-mini",
  "openrouter_api_key": ""
}
```

Credential lookup in `auto` provider mode:

1. Codex local OAuth auth at `~/.codex/auth.json` (or `$CODEX_HOME/auth.json`) via the installed `codex` CLI
2. `OPENROUTER_API_KEY` or `openrouter_api_key` from config
3. `OPENAI_API_KEY` or `openai_api_key` from config

OpenRouter example:

```bash
export OPENROUTER_API_KEY=sk-or-...
clai --provider openrouter "list files sorted by size"
```

## Usage

```bash
# Execute quietly by default
clai "git command to delete branch X"

# Print and execute
clai --print "git command to delete branch X"

# Only print the command
clai --dry "git command to delete branch X"
clai -n "find large files under this directory"

# Choose a model
clai --model gpt-5.4-mini "show current git branch"
clai --provider codex --model gpt-5.5 "show current git branch"
clai --provider openrouter --model openai/gpt-5.4-mini "show current git branch"

# Print the model explanation before the command
clai --explain --dry "compress this folder into archive.tar.gz"
```

## Safety

By default, `clai` executes exactly one command returned by the model without printing it first, which makes command substitution usable. Use `--print` to print before executing, or `--dry` to inspect without executing.
