#!/usr/bin/env python3
"""Manage the Claude Code settings file for the LiteLLM agent proxy.

This is the single writer for ``~/.claude/settings.json`` in this repository.
It replaces both the inline heredoc that used to live in ``setup.sh`` and the
``jq`` program in ``scripts/set-max-context.sh``.

Requires Python 3.11+ (the floor enforced by ``scripts/common.sh``).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import stat
import sys
import tempfile

ENV_KEY = "env"
BASE_URL_KEY = "ANTHROPIC_BASE_URL"
AUTH_TOKEN_KEY = "ANTHROPIC_AUTH_TOKEN"
MAX_CONTEXT_KEY = "CLAUDE_CODE_MAX_CONTEXT_TOKENS"

# Telemetry opt-outs. Set alongside the proxy keys because routing through a
# local proxy and still reporting usage upstream would defeat the point.
PRIVACY_ENV = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_GROWTHBOOK": "1",
}

SECRET_KEYS = frozenset({AUTH_TOKEN_KEY})
SECRET_PLACEHOLDER = "<hidden>"

DEFAULT_MODE = 0o600
TEMP_PREFIX = ".settings.json."

MANAGED_ONLY_NOTICE = (
    "Only the keys below are managed; every other setting is left as-is."
)


class ConfigError(Exception):
    """A condition that must leave the settings file untouched."""


def default_settings_path() -> str:
    """Return the settings path, honouring ``CLAUDE_SETTINGS_FILE``."""
    override = os.environ.get("CLAUDE_SETTINGS_FILE")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def load(path: str) -> tuple[dict, int | None]:
    """Read and validate the settings file.

    Returns the parsed object and the file's current permission bits. A missing
    or empty file yields an empty object, which is how a first run is handled.

    Raises:
        ConfigError: the path is a symlink, unreadable, not valid JSON, not a
            JSON object, or carries a non-object ``env`` key.
    """
    if os.path.islink(path):
        raise ConfigError(f"refusing to replace symlink: {path}")

    if not os.path.exists(path):
        return {}, None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = None

    if not raw.strip():
        return {}, mode

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} contains malformed JSON ({exc}); left unchanged"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"{path} does not contain a JSON object at the top level; left unchanged"
        )

    env = data.get(ENV_KEY)
    if env is not None and not isinstance(env, dict):
        raise ConfigError(f"{path} has a non-object '{ENV_KEY}' key; left unchanged")

    return data, mode


def save(path: str, data: dict, mode: int | None) -> None:
    """Write the settings file atomically, preserving its permission bits.

    Raises:
        ConfigError: the replacement could not be written.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp_path, mode if mode is not None else DEFAULT_MODE)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise ConfigError(f"failed to write {path}: {exc}") from exc


def render(data: dict) -> list[str]:
    """Render settings as diff-ready lines, with secret values masked."""
    redacted = dict(data)
    env = redacted.get(ENV_KEY)
    if isinstance(env, dict):
        redacted[ENV_KEY] = {
            key: (SECRET_PLACEHOLDER if key in SECRET_KEYS else value)
            for key, value in env.items()
        }
    return json.dumps(redacted, indent=2, sort_keys=True).splitlines()


def describe_changes(before: dict, after: dict) -> list[str]:
    """Summarise changed ``env`` keys as human-readable before/after lines.

    Values of secret keys are masked, so a changed token reads as a change
    without disclosing either value.
    """
    old_env = before.get(ENV_KEY) or {}
    new_env = after.get(ENV_KEY) or {}
    lines = []
    for key in sorted(set(old_env) | set(new_env)):
        old = old_env.get(key)
        new = new_env.get(key)
        if old == new:
            continue
        if key in SECRET_KEYS:
            # Both values are masked, so an arrow would convey nothing. State
            # the transition instead.
            if key not in old_env:
                lines.append(f"{ENV_KEY}.{key}: set (value hidden)")
            elif key not in new_env:
                lines.append(f"{ENV_KEY}.{key}: removed")
            else:
                lines.append(f"{ENV_KEY}.{key}: replaced (value hidden)")
        elif key not in old_env:
            lines.append(f"{ENV_KEY}.{key}: (unset) -> {json.dumps(new)}")
        elif key not in new_env:
            lines.append(f"{ENV_KEY}.{key}: {json.dumps(old)} -> (removed)")
        else:
            lines.append(
                f"{ENV_KEY}.{key}: {json.dumps(old)} -> {json.dumps(new)}"
            )
    return lines


def confirm(path: str, before: dict, after: dict) -> bool:
    """Show the pending change and ask for confirmation.

    Returns True when the user accepts. A non-interactive stdin declines
    rather than assuming consent.
    """
    diff = list(
        difflib.unified_diff(
            render(before),
            render(after),
            fromfile=f"{path} (current)",
            tofile=f"{path} (proposed)",
            lineterm="",
        )
    )
    print(f"The following changes will be applied to {path}:\n")
    print(f"  {MANAGED_ONLY_NOTICE}\n")
    for line in diff:
        print(f"  {line}")

    changes = describe_changes(before, after)
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


def apply(path: str, mutate, interactive: bool, unchanged_message: str) -> int:
    """Load, mutate, and conditionally persist the settings file.

    Args:
        path: Settings file to operate on.
        mutate: Callable receiving a deep-ish copy of the settings and
            returning the desired state.
        interactive: Whether to require confirmation before writing.
        unchanged_message: Printed when the mutation is a no-op.

    Returns:
        A process exit code.
    """
    try:
        current, mode = load(path)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    proposed = json.loads(json.dumps(current))
    proposed = mutate(proposed)

    if proposed == current:
        print(unchanged_message)
        return 0

    if interactive and not confirm(path, current, proposed):
        print("No changes were written.")
        return 1

    try:
        save(path, proposed, mode)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Updated {path}")
    return 0


def set_proxy(base_url: str, token: str):
    """Build a mutation that points Claude Code at the local proxy."""

    def mutate(data: dict) -> dict:
        env = data.get(ENV_KEY)
        if not isinstance(env, dict):
            env = {}
        env[BASE_URL_KEY] = base_url
        env[AUTH_TOKEN_KEY] = token
        env.update(PRIVACY_ENV)
        data[ENV_KEY] = env
        return data

    return mutate


def set_max_context(value: str):
    """Build a mutation that sets the context-window override."""

    def mutate(data: dict) -> dict:
        env = data.get(ENV_KEY)
        if not isinstance(env, dict):
            env = {}
        env[MAX_CONTEXT_KEY] = value
        data[ENV_KEY] = env
        return data

    return mutate


def delete_max_context(data: dict) -> dict:
    """Remove the context-window override, leaving everything else intact."""
    env = data.get(ENV_KEY)
    if isinstance(env, dict):
        env.pop(MAX_CONTEXT_KEY, None)
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure Claude Code for the LiteLLM agent proxy.",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="settings file to modify (default: ~/.claude/settings.json)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--base-url",
        help="proxy base URL to write to env.ANTHROPIC_BASE_URL",
    )
    group.add_argument(
        "--max-context",
        metavar="TOKENS",
        help=f"set env.{MAX_CONTEXT_KEY}",
    )
    group.add_argument(
        "--delete-max-context",
        action="store_true",
        help=f"remove env.{MAX_CONTEXT_KEY}",
    )
    parser.add_argument(
        "--token",
        help="auth token to write to env.ANTHROPIC_AUTH_TOKEN (with --base-url)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation prompt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = args.settings or default_settings_path()

    if args.base_url:
        if not args.token:
            parser.error("--base-url requires --token")
        return apply(
            path,
            set_proxy(args.base_url, args.token),
            interactive=not args.yes,
            unchanged_message=f"Claude Code is already configured in {path}",
        )

    if args.max_context:
        if not args.max_context.isdigit() or args.max_context.lstrip("0") != args.max_context:
            parser.error(
                "use a positive integer token count (e.g. 272000), "
                "without suffixes or leading zeros"
            )
        # Non-interactive by design: this is an explicit single-purpose command,
        # invoked through the documented `ccmax` alias, not part of setup.
        return apply(
            path,
            set_max_context(args.max_context),
            interactive=False,
            unchanged_message=f"{MAX_CONTEXT_KEY} is already {args.max_context}",
        )

    return apply(
        path,
        delete_max_context,
        interactive=False,
        unchanged_message="Context override is already absent.",
    )


if __name__ == "__main__":
    sys.exit(main())
