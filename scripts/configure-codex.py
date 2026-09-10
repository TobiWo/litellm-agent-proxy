#!/usr/bin/env python3
"""Manage the Codex CLI config for the LiteLLM agent proxy.

Codex stores its settings as TOML in ``~/.codex/config.toml``, a hand-written
file that typically holds comments and many unrelated tables. The Python
standard library can read TOML (``tomllib``) but cannot write it, and a
round-trip through a third-party writer would discard comments and ordering.

This module therefore parses the file to decide *what* must change, then
applies those decisions as targeted line edits while tracking which table each
line belongs to. Unrelated lines are never rewritten, and the result is
re-parsed before it replaces the original.

Requires Python 3.11+ (the floor enforced by ``scripts/common.sh``).
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import tempfile
import tomllib

PROVIDER_NAME = "litellm"
PROVIDER_TABLE = f"model_providers.{PROVIDER_NAME}"
API_KEY_VAR = "LITELLM_API_KEY"
DEFAULT_MODEL = "gpt-5-6-luna"
DEFAULT_MODE = 0o600
TEMP_PREFIX = ".config.toml."

# Codex's bundled catalog tags gpt-5.6-*/gpt-6 models `tool_mode:
# "code_mode_only"` (stripped by update-codex-catalog.py), which otherwise
# force-selects the JS "code mode" `exec` tool. GitHub Copilot's /responses
# backend doesn't implement that tool's `custom` type correctly, so every
# command aborts. Disabling code_mode here restores the plain shell/local_shell
# tool that Codex falls back to once tool_mode is absent.
CODE_MODE_TABLE = "features.code_mode"

MANAGED_ONLY_NOTICE = (
    "Only the keys below are managed; every other setting, comment, and "
    "section is left as-is."
)

MODEL_KEY = "model"
PROVIDER_KEY = "model_provider"
CATALOG_KEY = "model_catalog_json"
WEB_SEARCH_KEY = "web_search"
WEB_SEARCH_VALUE = "disabled"

# A table header, e.g. `[model_providers.litellm]`. Array-of-table headers
# (`[[x]]`) are matched too so that walking never mistakes one for a key.
TABLE_RE = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*(?:#.*)?$")


class ConfigError(Exception):
    """A condition that must leave the config file untouched."""


def default_config_path() -> str:
    """Return the Codex config path, honouring ``CODEX_HOME``."""
    codex_home = os.environ.get("CODEX_HOME")
    if not codex_home:
        codex_home = os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(codex_home, "config.toml")


def render_value(value: str | bool) -> str:
    """Render a TOML value: a basic string, or a bare boolean literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def key_pattern(key: str) -> re.Pattern[str]:
    """Match an assignment to ``key``, bare or quoted, at line start."""
    return re.compile(rf'^\s*(?:{re.escape(key)}|"{re.escape(key)}")\s*=')


def trailing_comment(line: str) -> str:
    """Return the inline comment on an assignment line, or an empty string.

    A ``#`` inside the quoted value is not a comment, so the scan tracks
    quoting rather than taking the first ``#`` it sees.
    """
    in_string = False
    quote_char = ""
    index = 0
    while index < len(line):
        char = line[index]
        if in_string:
            if char == "\\" and quote_char == '"':
                index += 2
                continue
            if char == quote_char:
                in_string = False
        elif char in "\"'":
            in_string = True
            quote_char = char
        elif char == "#":
            return line[index:]
        index += 1
    return ""


def assign(key: str, value: str | bool, previous: str = "") -> str:
    """Render ``key = value``, carrying over any inline comment.

    Rewriting a line must not silently discard the comment a user wrote next
    to it. The comment is re-attached at the column it previously started on
    where the new value still fits, and after a single space otherwise.
    """
    rendered = f"{key} = {render_value(value)}"
    comment = trailing_comment(previous) if previous else ""
    if not comment:
        return rendered
    column = previous.index(comment)
    padding = max(column - len(rendered), 1)
    return rendered + " " * padding + comment


def parse(path: str) -> dict:
    """Parse the config file, returning an empty mapping when absent.

    Raises:
        ConfigError: the file is a symlink, unreadable, or not valid TOML.
    """
    if os.path.islink(path):
        raise ConfigError(f"refusing to replace symlink: {path}")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} contains malformed TOML ({exc}); left unchanged") from exc


def responses_models(litellm_config: str) -> dict[str, str]:
    """Map each Responses-API alias to its upstream GitHub Copilot slug.

    The LiteLLM config is scanned textually rather than with a YAML parser to
    avoid adding a dependency. Each ``- model_name:`` starts an entry; the
    entry is kept when it carries both ``mode: responses`` and a
    ``model: github_copilot/<slug>`` line before the next entry begins.

    The upstream slug matters because it is not derivable from the alias:
    ``gpt-6`` maps to ``gpt-6-astra``.
    """
    models: dict[str, str] = {}
    alias = None
    is_responses = False
    slug = None

    def flush():
        if alias and is_responses and slug:
            models[alias] = slug

    try:
        with open(litellm_config, "r", encoding="utf-8") as handle:
            for line in handle:
                name = re.match(r"^\s*-\s*model_name:\s*(\S+)", line)
                if name:
                    flush()
                    alias = name.group(1).strip().strip("\"'")
                    is_responses = False
                    slug = None
                    continue
                if alias is None:
                    continue
                if re.match(r"^\s*mode:\s*responses\s*$", line):
                    is_responses = True
                    continue
                upstream = re.match(r"^\s*model:\s*github_copilot/(\S+)", line)
                if upstream:
                    slug = upstream.group(1).strip().strip("\"'")
    except OSError as exc:
        raise ConfigError(f"cannot read {litellm_config}: {exc}") from exc

    flush()
    return models


def responses_aliases(litellm_config: str) -> list[str]:
    """Return the model aliases served over the Responses API."""
    return list(responses_models(litellm_config))


def resolve_model(current, aliases: list[str]) -> tuple[str, str | None]:
    """Decide the model value to write.

    GitHub Copilot spells models with dots (``gpt-5.3-codex``) while the
    LiteLLM aliases use dashes, so a dotted spelling of a supported model is
    corrected silently. Anything the proxy cannot serve over the Responses API
    falls back to the default and reports why.

    Returns:
        The model to write, and a warning to show (or None when silent).
    """
    if not isinstance(current, str) or not current.strip():
        return DEFAULT_MODEL, None

    normalized = current.replace(".", "-")
    if normalized in aliases:
        return normalized, None

    return DEFAULT_MODEL, (
        f"model {current!r} is not available through this proxy; "
        f"set to {DEFAULT_MODEL!r}. Available: {', '.join(aliases)}"
    )


def desired_settings(base_url: str, catalog: str, model: str) -> tuple[dict, dict, dict]:
    """Return the managed top-level scalars, provider-table, and code-mode-table keys."""
    scalars = {
        MODEL_KEY: model,
        PROVIDER_KEY: PROVIDER_NAME,
        CATALOG_KEY: catalog,
        WEB_SEARCH_KEY: WEB_SEARCH_VALUE,
    }
    provider = {
        "name": "LiteLLM Proxy",
        "base_url": base_url,
        "env_key": API_KEY_VAR,
        "wire_api": "responses",
    }
    code_mode = {"enabled": False}
    return scalars, provider, code_mode


def table_spans(lines: list[str]) -> tuple[int, dict[str, tuple[int, int]]]:
    """Locate the top-level region and every table's line span.

    Returns:
        The index of the first table header (or len(lines) when there is
        none), and a mapping of table name to a half-open [start, end) range
        covering the lines beneath its header.
    """
    first_header = len(lines)
    spans: dict[str, tuple[int, int]] = {}
    current_name = None
    current_start = 0

    for index, line in enumerate(lines):
        match = TABLE_RE.match(line)
        if not match:
            continue
        if first_header == len(lines):
            first_header = index
        if current_name is not None:
            spans.setdefault(current_name, (current_start, index))
        current_name = match.group(1).strip()
        current_start = index + 1

    if current_name is not None:
        spans.setdefault(current_name, (current_start, len(lines)))

    return first_header, spans


def edit_scalars(lines: list[str], scalars: dict[str, str], first_header: int) -> list[str]:
    """Replace or insert the managed top-level keys.

    Only the region above the first table header is considered: an identically
    named key inside a table belongs to that table and must not be touched.
    """
    result = list(lines)
    pending = dict(scalars)

    for index in range(min(first_header, len(result))):
        for key in list(pending):
            if key_pattern(key).match(result[index]):
                result[index] = assign(key, pending.pop(key), result[index])
                break

    if not pending:
        return result

    # Insert after the last non-blank line of the top-level region so new keys
    # stay above the first table header, as TOML requires.
    insert_at = first_header if first_header <= len(result) else len(result)
    while insert_at > 0 and not result[insert_at - 1].strip():
        insert_at -= 1

    additions = [assign(key, value) for key, value in pending.items()]
    return result[:insert_at] + additions + result[insert_at:]


def edit_table(lines: list[str], table_name: str, values: dict[str, str | bool]) -> list[str]:
    """Replace a table's keys, or append the table when absent."""
    _, spans = table_spans(lines)
    span = spans.get(table_name)

    if span is None:
        block = [""] if lines and lines[-1].strip() else []
        block.append(f"[{table_name}]")
        block.extend(assign(key, value) for key, value in values.items())
        return list(lines) + block

    start, end = span
    result = list(lines)
    pending = dict(values)

    for index in range(start, end):
        for key in list(pending):
            if key_pattern(key).match(result[index]):
                result[index] = assign(key, pending.pop(key), result[index])
                break

    if pending:
        insert_at = end
        while insert_at > start and not result[insert_at - 1].strip():
            insert_at -= 1
        additions = [assign(key, value) for key, value in pending.items()]
        result = result[:insert_at] + additions + result[insert_at:]

    return result


def verify(
    content: str,
    scalars: dict[str, str],
    provider: dict[str, str],
    code_mode: dict[str, bool],
) -> None:
    """Re-parse edited content and confirm every managed key landed.

    This is the guard that makes line-level editing safe: if the edit produced
    invalid TOML, or a key did not take the intended value, the caller aborts
    rather than replacing a working config with a broken one.

    Raises:
        ConfigError: the result is unparseable or a managed key is wrong.
    """
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"the edit produced invalid TOML ({exc}); nothing was written") from exc

    for key, value in scalars.items():
        if parsed.get(key) != value:
            raise ConfigError(
                f"post-edit check failed: {key} is {parsed.get(key)!r}, expected {value!r}"
            )

    table = parsed.get("model_providers", {}).get(PROVIDER_NAME)
    if not isinstance(table, dict):
        raise ConfigError(f"post-edit check failed: [{PROVIDER_TABLE}] is missing")
    for key, value in provider.items():
        if table.get(key) != value:
            raise ConfigError(
                f"post-edit check failed: {PROVIDER_TABLE}.{key} is "
                f"{table.get(key)!r}, expected {value!r}"
            )

    table = parsed.get("features", {}).get("code_mode")
    if not isinstance(table, dict):
        raise ConfigError(f"post-edit check failed: [{CODE_MODE_TABLE}] is missing")
    for key, value in code_mode.items():
        if table.get(key) != value:
            raise ConfigError(
                f"post-edit check failed: {CODE_MODE_TABLE}.{key} is "
                f"{table.get(key)!r}, expected {value!r}"
            )


def build(path: str, scalars: dict[str, str], provider: dict[str, str], code_mode: dict[str, bool]) -> str:
    """Produce the updated file content."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                original = handle.read()
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        lines = original.splitlines()
    else:
        lines = []

    first_header, _ = table_spans(lines)
    lines = edit_scalars(lines, scalars, first_header)
    lines = edit_table(lines, PROVIDER_TABLE, provider)
    lines = edit_table(lines, CODE_MODE_TABLE, code_mode)
    return "\n".join(lines) + "\n"


def write(path: str, content: str) -> None:
    """Write the config atomically, preserving permissions when they exist."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    mode = DEFAULT_MODE
    if os.path.exists(path):
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            mode = DEFAULT_MODE

    fd, tmp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise ConfigError(f"failed to write {path}: {exc}") from exc


def report_divergence(
    existing: dict,
    scalars: dict[str, str],
    provider: dict[str, str],
    code_mode: dict[str, bool],
) -> list[str]:
    """Describe managed keys whose values are about to change."""
    lines = []
    for key, value in scalars.items():
        current = existing.get(key)
        if current is None:
            lines.append(f"{key}: (unset) -> {value!r}")
        elif current != value:
            lines.append(f"{key}: {current!r} -> {value!r}")

    table = existing.get("model_providers", {})
    table = table.get(PROVIDER_NAME, {}) if isinstance(table, dict) else {}
    for key, value in provider.items():
        current = table.get(key) if isinstance(table, dict) else None
        if current is None:
            lines.append(f"{PROVIDER_TABLE}.{key}: (unset) -> {value!r}")
        elif current != value:
            lines.append(f"{PROVIDER_TABLE}.{key}: {current!r} -> {value!r}")

    table = existing.get("features", {})
    table = table.get("code_mode", {}) if isinstance(table, dict) else {}
    for key, value in code_mode.items():
        current = table.get(key) if isinstance(table, dict) else None
        if current is None:
            lines.append(f"{CODE_MODE_TABLE}.{key}: (unset) -> {value!r}")
        elif current != value:
            lines.append(f"{CODE_MODE_TABLE}.{key}: {current!r} -> {value!r}")
    return lines


def confirm(path: str, before: str, after: str, changes: list[str]) -> bool:
    """Show the pending diff and ask for confirmation."""
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{path} (current)",
            tofile=f"{path} (proposed)",
            lineterm="",
            n=2,
        )
    )
    print(f"The following changes will be applied to {path}:\n")
    print(f"  {MANAGED_ONLY_NOTICE}\n")
    for line in diff:
        print(f"  {line}")

    if changes:
        print("\nValues being overwritten:")
        for line in changes:
            print(f"  {line}")

    if not sys.stdin.isatty():
        print("\nstdin is not a terminal; refusing to write without confirmation.")
        return False

    try:
        reply = input("\nApply these settings? [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Configure the Codex CLI for the LiteLLM agent proxy.",
    )
    parser.add_argument("--base-url", required=True, help="proxy base URL including /v1")
    parser.add_argument("--catalog", required=True, help="absolute path to the Codex model catalog")
    parser.add_argument("--litellm-config", required=True, help="path to the LiteLLM config")
    parser.add_argument("--config", default=None, help="Codex config file (default: ~/.codex/config.toml)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    path = args.config or default_config_path()

    try:
        existing = parse(path)
        aliases = responses_aliases(args.litellm_config)
        if not aliases:
            raise ConfigError(
                f"no models with 'mode: responses' found in {args.litellm_config}; "
                "Codex can only use Responses-API models"
            )

        model, warning = resolve_model(existing.get(MODEL_KEY), aliases)
        scalars, provider, code_mode = desired_settings(args.base_url, args.catalog, model)

        changes = report_divergence(existing, scalars, provider, code_mode)
        if not changes:
            print(f"Codex is already configured in {path}")
            if warning:
                print(f"Warning: {warning}")
            return 0

        original = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                original = handle.read()

        updated = build(path, scalars, provider, code_mode)
        verify(updated, scalars, provider, code_mode)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if warning:
        print(f"Warning: {warning}\n")

    if not args.yes and not confirm(path, original, updated, changes):
        print("No changes were written.")
        return 1

    try:
        write(path, updated)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
