#!/usr/bin/env python3
"""Regenerate the Codex model catalog for the installed Codex version.

Codex ships a bundled catalog whose schema is tied to its release. This script
rebuilds ``codex/codex-model-catalog.json`` from that bundle so the vendored
file tracks the installed Codex, rather than being hand-edited after upgrades.

The bundled catalog is the single source of truth for *which* models exist: an
entry is emitted only when the model is both a Responses-API alias in the
LiteLLM config and present in the bundle. A model Codex has dropped simply
disappears — as model generations churn, that keeps maintenance bounded.

GitHub Copilot supplies the real context limits, which differ from the numbers
Codex bundles for OpenAI-hosted serving.

Requires Python 3.11+ (the floor enforced by ``scripts/common.sh``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

# Fields that only make sense for OpenAI's own hosting; dropped from the
# vendored catalog because the proxy serves these models through Copilot.
STRIPPED_FIELDS = frozenset(
    {
        "additional_speed_tiers",
        "base_instructions",
        "comp_hash",
        "service_tiers",
        "supports_search_tool",
        "use_responses_lite",
        # Forces Codex's JS "code mode" exec tool, which GitHub Copilot's
        # /responses backend answers with a plain function_call instead of
        # the expected custom_tool_call, aborting every command. Dropping it
        # lets Codex fall back to the standard shell/local_shell tool.
        "tool_mode",
    }
)

CONTEXT_KEY = "context_window"
MAX_CONTEXT_KEY = "max_context_window"
SLUG_KEY = "slug"
MODELS_KEY = "models"
DEFAULT_MODE = 0o644
TEMP_PREFIX = ".codex-model-catalog.json."


class CatalogError(Exception):
    """A condition that must leave the catalog untouched."""


def load_sibling(name: str, filename: str):
    """Import a helper module that sits next to this script."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CatalogError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundled_models(codex_bin: str) -> dict:
    """Return the catalog Codex ships, keyed by upstream slug.

    Raises:
        CatalogError: Codex is missing, failed, or returned unusable JSON.
    """
    try:
        result = subprocess.run(
            [codex_bin, "debug", "models", "--bundled"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CatalogError(f"{codex_bin} is not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CatalogError(f"{codex_bin} debug models timed out") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:200]
        raise CatalogError(f"{codex_bin} debug models failed: {detail}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{codex_bin} returned invalid JSON: {exc}") from exc

    models = payload.get(MODELS_KEY)
    if not isinstance(models, list) or not models:
        raise CatalogError(f"{codex_bin} returned no bundled models")

    return {m[SLUG_KEY]: m for m in models if isinstance(m, dict) and SLUG_KEY in m}


def copilot_limits(path: str) -> dict[str, dict]:
    """Return each Copilot model's token limits, keyed by model id.

    Raises:
        CatalogError: the file is unreadable, invalid, or has no models.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path} is not valid JSON: {exc}") from exc

    models = payload.get("data") or payload.get(MODELS_KEY)
    if not isinstance(models, list) or not models:
        raise CatalogError(f"{path} contains no models; the fetch likely failed")

    limits = {}
    for model in models:
        if not isinstance(model, dict) or "id" not in model:
            continue
        capabilities = model.get("capabilities") or {}
        limits[model["id"]] = capabilities.get("limits") or {}
    return limits


def build_entry(template: dict, alias: str, limits: dict) -> dict:
    """Shape one catalog entry from its bundled template and Copilot limits.

    ``context_window`` is Copilot's prompt budget rather than its total window:
    the total includes generation headroom the prompt cannot use.

    Raises:
        CatalogError: Copilot did not report the limits this entry needs.
    """
    prompt_tokens = limits.get("max_prompt_tokens")
    window_tokens = limits.get("max_context_window_tokens")
    if not prompt_tokens or not window_tokens:
        raise CatalogError(f"Copilot reported no token limits for {alias}")

    entry = {k: v for k, v in template.items() if k not in STRIPPED_FIELDS}
    entry[SLUG_KEY] = alias
    entry[CONTEXT_KEY] = prompt_tokens
    entry[MAX_CONTEXT_KEY] = window_tokens
    return entry


def build_catalog(models: dict[str, str], bundled: dict, limits: dict) -> tuple[dict, list[str]]:
    """Assemble the catalog, reporting models that had to be skipped.

    Returns:
        The catalog payload and a list of human-readable skip reasons.
    """
    entries = []
    skipped = []

    for alias, slug in models.items():
        if slug not in bundled:
            skipped.append(f"{alias}: Codex no longer bundles '{slug}'")
            continue
        if slug not in limits:
            skipped.append(f"{alias}: GitHub Copilot does not serve '{slug}'")
            continue
        try:
            entries.append(build_entry(bundled[slug], alias, limits[slug]))
        except CatalogError as exc:
            skipped.append(f"{alias}: {exc}")

    return {MODELS_KEY: entries}, skipped


def summarise(before: dict, after: dict) -> list[str]:
    """Describe how the regenerated catalog differs from the current one."""
    old = {m.get(SLUG_KEY): m for m in before.get(MODELS_KEY, [])}
    new = {m.get(SLUG_KEY): m for m in after.get(MODELS_KEY, [])}
    lines = []

    for slug in sorted(set(new) - set(old)):
        lines.append(f"added:   {slug}")
    for slug in sorted(set(old) - set(new)):
        lines.append(f"removed: {slug}")
    for slug in sorted(set(old) & set(new)):
        changed = [
            f"{key} {old[slug].get(key)} -> {new[slug].get(key)}"
            for key in (CONTEXT_KEY, MAX_CONTEXT_KEY)
            if old[slug].get(key) != new[slug].get(key)
        ]
        if changed:
            lines.append(f"changed: {slug} ({', '.join(changed)})")
        elif old[slug] != new[slug]:
            lines.append(f"changed: {slug} (schema fields updated)")
    return lines


def render(catalog: dict) -> str:
    """Serialise the catalog the way the vendored file is formatted."""
    return json.dumps(catalog, indent=2) + "\n"


def write(path: str, content: str) -> None:
    """Write the catalog atomically, preserving existing permissions."""
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
        raise CatalogError(f"failed to write {path}: {exc}") from exc


def read_current(path: str) -> dict:
    """Return the existing catalog, or an empty one when absent/unreadable."""
    if not os.path.exists(path):
        return {MODELS_KEY: []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {MODELS_KEY: []}


def confirm(path: str, changes: list[str]) -> bool:
    """Show what would change and ask for confirmation."""
    print(f"The following changes will be applied to {path}:\n")
    for line in changes:
        print(f"  {line}")

    if not sys.stdin.isatty():
        print("\nstdin is not a terminal; refusing to write without confirmation.")
        return False

    try:
        reply = input("\nApply these changes? [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the Codex model catalog for the installed Codex version.",
    )
    parser.add_argument("--litellm-config", required=True, help="path to the LiteLLM config")
    parser.add_argument("--copilot-models", required=True, help="path to a fetched Copilot catalog")
    parser.add_argument("--catalog", required=True, help="catalog file to regenerate")
    parser.add_argument("--output", default=None, help="write elsewhere instead (for review)")
    parser.add_argument("--codex-bin", default="codex", help="Codex executable to query")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    destination = args.output or args.catalog

    try:
        codex = load_sibling("configure_codex", "configure-codex.py")
        models = codex.responses_models(args.litellm_config)
        if not models:
            raise CatalogError(
                f"no models with 'mode: responses' found in {args.litellm_config}"
            )

        catalog, skipped = build_catalog(
            models, bundled_models(args.codex_bin), copilot_limits(args.copilot_models)
        )

        # An empty catalog would make every model fall back to generic
        # metadata. That is almost always a failed fetch rather than an
        # intentional state, so refuse rather than silently emptying the file.
        if not catalog[MODELS_KEY]:
            raise CatalogError(
                "no models could be resolved; refusing to write an empty catalog"
            )
    except CatalogError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for line in skipped:
        print(f"Warning: skipped {line}")
    if skipped:
        print()

    content = render(catalog)
    changes = summarise(read_current(args.catalog), catalog)

    if not changes and os.path.exists(destination):
        with open(destination, "r", encoding="utf-8") as handle:
            if handle.read() == content:
                print(f"{destination} is already up to date")
                return 0

    if not changes:
        changes = ["(formatting only)"]

    if not args.yes and not confirm(destination, changes):
        print("No changes were written.")
        return 1

    try:
        write(destination, content)
    except CatalogError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {len(catalog[MODELS_KEY])} models to {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
