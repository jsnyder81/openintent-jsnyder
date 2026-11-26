#!/usr/bin/env python3
"""Validate that each release model's YAML source and JSON Schema are deep-equal."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
YAML_REL_PATH = Path("src") / "openintent-wifi.yaml"
JSON_REL_PATH = Path("oi-wifi.schema.json")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML SafeLoader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _format_key_token(key: Any, colliding_str_keys: set[str]) -> str:
    str_key = str(key)
    if not isinstance(key, str) or str_key in colliding_str_keys:
        return _escape_pointer_token(f"{key!r}({type(key).__name__})")
    return _escape_pointer_token(str_key)


def _format_value(value: Any, max_len: int = 120) -> str:
    rendered = repr(value)
    if len(rendered) > max_len:
        return rendered[: max_len - 3] + "..."
    return rendered


def diff_values(yaml_val: Any, json_val: Any, path: str = "") -> list[str]:
    """Return path-style diff lines between yaml_val and json_val."""
    display_path = path or "/"

    if type(yaml_val) is not type(json_val):
        return [
            f"{display_path}: yaml={_format_value(yaml_val)} json={_format_value(json_val)}"
        ]

    if isinstance(yaml_val, dict):
        diffs: list[str] = []
        all_keys = sorted(
            set(yaml_val.keys()) | set(json_val.keys()),
            key=lambda k: (str(k), type(k).__qualname__),
        )
        str_counts = Counter(str(k) for k in all_keys)
        colliding_str_keys = {s for s, count in str_counts.items() if count > 1}
        for key in all_keys:
            child_path = f"{path}/{_format_key_token(key, colliding_str_keys)}"
            in_yaml = key in yaml_val
            in_json = key in json_val
            if in_yaml and not in_json:
                diffs.append(
                    f"{child_path}: yaml={_format_value(yaml_val[key])} json=<missing>"
                )
            elif in_json and not in_yaml:
                diffs.append(
                    f"{child_path}: yaml=<missing> json={_format_value(json_val[key])}"
                )
            else:
                diffs.extend(diff_values(yaml_val[key], json_val[key], child_path))
        return diffs

    if isinstance(yaml_val, list):
        diffs = []
        shared_len = min(len(yaml_val), len(json_val))
        for idx in range(shared_len):
            diffs.extend(diff_values(yaml_val[idx], json_val[idx], f"{path}/{idx}"))
        if len(yaml_val) > shared_len:
            for idx in range(shared_len, len(yaml_val)):
                diffs.append(
                    f"{path}/{idx}: yaml={_format_value(yaml_val[idx])} json=<missing>"
                )
        elif len(json_val) > shared_len:
            for idx in range(shared_len, len(json_val)):
                diffs.append(
                    f"{path}/{idx}: yaml=<missing> json={_format_value(json_val[idx])}"
                )
        return diffs

    if yaml_val != json_val:
        return [
            f"{display_path}: yaml={_format_value(yaml_val)} json={_format_value(json_val)}"
        ]

    return []


def validate_model_dir(model_dir: Path) -> tuple[bool, list[str]]:
    """Validate a single release/*/models directory."""
    yaml_path = model_dir / YAML_REL_PATH
    json_path = model_dir / JSON_REL_PATH

    errors: list[str] = []
    if not yaml_path.is_file():
        errors.append(f"missing YAML source file: {yaml_path}")
    if not json_path.is_file():
        errors.append(f"missing JSON schema file: {json_path}")
    if errors:
        return False, errors

    try:
        with yaml_path.open("r", encoding="utf-8") as fh:
            yaml_data = yaml.load(fh, Loader=_UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        return False, [f"failed to parse YAML {yaml_path}: {exc}"]

    try:
        with json_path.open("r", encoding="utf-8") as fh:
            json_data = json.load(fh, object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, [f"failed to parse JSON {json_path}: {exc}"]

    diffs = diff_values(yaml_data, json_data)
    return (len(diffs) == 0), diffs


def discover_model_dirs() -> list[Path]:
    """Auto-discover release/*/models directories relative to the repo root."""
    return sorted(p for p in (REPO_ROOT / "release").glob("*/models") if p.is_dir())


def _display_dir(model_dir: Path) -> str:
    try:
        return str(model_dir.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(model_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate deep-equality between src/openintent-wifi.yaml and "
            "oi-wifi.schema.json in release model directories."
        )
    )
    parser.add_argument(
        "model_dirs",
        nargs="*",
        type=Path,
        help="Model directories to validate (default: auto-discover release/*/models).",
    )
    args = parser.parse_args(argv)

    model_dirs = args.model_dirs if args.model_dirs else discover_model_dirs()
    if not model_dirs:
        print("FAIL: no release/*/models directories found", file=sys.stderr)
        return 1

    all_passed = True
    for model_dir in model_dirs:
        passed, details = validate_model_dir(model_dir)
        label = _display_dir(model_dir)
        if passed:
            print(f"PASS: {label}")
        else:
            all_passed = False
            print(f"FAIL: {label}")
            for line in details:
                print(f"  {line}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
