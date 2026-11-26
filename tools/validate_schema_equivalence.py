#!/usr/bin/env python3
"""Validate that each release model's YAML source and JSON Schema are deep-equal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

YAML_REL_PATH = Path("src") / "openintent-wifi.yaml"
JSON_REL_PATH = Path("oi-wifi.schema.json")


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _format_value(value: Any, max_len: int = 120) -> str:
    rendered = repr(value)
    if len(rendered) > max_len:
        return rendered[: max_len - 3] + "..."
    return rendered


def diff_values(yaml_val: Any, json_val: Any, path: str = "") -> list[str]:
    """Return path-style diff lines between yaml_val and json_val."""
    display_path = path or "/"

    # Guard against Python's bool-is-int subclassing (True == 1, False == 0).
    if isinstance(yaml_val, bool) != isinstance(json_val, bool):
        return [
            f"{display_path}: yaml={_format_value(yaml_val)} json={_format_value(json_val)}"
        ]

    if isinstance(yaml_val, dict) and isinstance(json_val, dict):
        diffs: list[str] = []
        all_keys = sorted(set(yaml_val.keys()) | set(json_val.keys()), key=str)
        for key in all_keys:
            child_path = f"{path}/{_escape_pointer_token(str(key))}"
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

    if isinstance(yaml_val, list) and isinstance(json_val, list):
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

    if type(yaml_val) is not type(json_val) and not (
        isinstance(yaml_val, (int, float)) and isinstance(json_val, (int, float))
    ):
        return [
            f"{display_path}: yaml={_format_value(yaml_val)} json={_format_value(json_val)}"
        ]

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
            yaml_data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        return False, [f"failed to parse YAML {yaml_path}: {exc}"]

    try:
        with json_path.open("r", encoding="utf-8") as fh:
            json_data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"failed to parse JSON {json_path}: {exc}"]

    diffs = diff_values(yaml_data, json_data)
    return (len(diffs) == 0), diffs


def discover_model_dirs() -> list[Path]:
    """Auto-discover release/*/models directories."""
    cwd_release = Path("release")
    if cwd_release.is_dir():
        return sorted(p for p in cwd_release.glob("*/models") if p.is_dir())

    repo_root = Path(__file__).resolve().parent.parent
    return sorted(p for p in (repo_root / "release").glob("*/models") if p.is_dir())


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
        if passed:
            print(f"PASS: {model_dir}")
        else:
            all_passed = False
            print(f"FAIL: {model_dir}")
            for line in details:
                print(f"  {line}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
