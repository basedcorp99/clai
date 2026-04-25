# clai

Command Line Artificial Intelligence: ask for a shell command in plain English, print it, then run it.

```bash
clai "git command to delete branch feature/foo"
# prints and executes something like:
# git branch -d feature/foo
```

## Install

From this directory:

```bash
python -m pip install -e .
```

## Credentials

`clai` uses credentials in this order:

1. Codex local OAuth auth at `~/.codex/auth.json` (or `$CODEX_HOME/auth.json`) via the installed `codex` CLI
2. `OPENROUTER_API_KEY`
3. `OPENAI_API_KEY` as an extra fallback

OpenRouter example:

```bash
export OPENROUTER_API_KEY=sk-or-...
clai --provider openrouter "list files sorted by size"
```

## Usage

```bash
# Print and execute
clai "git command to delete branch X"

# Only print the command
clai --dry "git command to delete branch X"
clai -n "find large files under this directory"

# Show provider/model and timing breakdown
clai --dry --timing "git command to delete branch X"

# Choose a model
clai --model gpt-4.1-nano "show current git branch"
clai --provider codex --model gpt-5.4-mini "show current git branch"
clai --provider openrouter --model openai/gpt-4.1-nano "show current git branch"

# Print the model explanation too
clai --explain --dry "compress this folder into archive.tar.gz"
```

## Safety

By default, `clai` executes exactly one command returned by the model. Use `--dry` when you want to inspect the command first.
