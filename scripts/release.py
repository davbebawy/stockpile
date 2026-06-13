#!/usr/bin/env python3
"""Bump the Stockpile version, commit, tag, and push a GitHub release.

Usage:
    python3 scripts/release.py 0.6.0 "Short release description"

What it does:
    1. Validates the version string (MAJOR.MINOR.PATCH).
    2. Updates the version in:
         - custom_components/stockpile/manifest.json
         - custom_components/stockpile/const.py
         - custom_components/stockpile/frontend/stockpile-card.js
    3. Commits the changes with the message "vX.Y.Z".
    4. Creates an annotated git tag vX.Y.Z.
    5. Pushes the commit and tag to origin.
    6. Creates a GitHub release via `gh release create`.

Requirements: git, gh (GitHub CLI, authenticated).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], **kwargs) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"FAIL: {' '.join(cmd)}\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def _check_clean():
    status = _run(["git", "status", "--porcelain"], cwd=ROOT)
    if status:
        print("Working tree is not clean. Commit or stash changes first.", file=sys.stderr)
        sys.exit(1)


def _bump_manifest(version: str) -> None:
    path = ROOT / "custom_components/stockpile/manifest.json"
    data = json.loads(path.read_text())
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n")


def _bump_const(version: str) -> None:
    path = ROOT / "custom_components/stockpile/const.py"
    text = path.read_text()
    updated = re.sub(r'^VERSION\s*=\s*"[^"]+"', f'VERSION = "{version}"', text, flags=re.M)
    path.write_text(updated)


def _bump_js(version: str) -> None:
    path = ROOT / "custom_components/stockpile/frontend/stockpile-card.js"
    text = path.read_text()
    updated = re.sub(r'^const VERSION\s*=\s*"[^"]+"', f'const VERSION = "{version}"', text, flags=re.M)
    path.write_text(updated)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: release.py VERSION DESCRIPTION", file=sys.stderr)
        sys.exit(1)

    version = sys.argv[1].strip().lstrip("v")
    description = sys.argv[2].strip()

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"Version must be MAJOR.MINOR.PATCH, got: {version!r}", file=sys.stderr)
        sys.exit(1)

    tag = f"v{version}"

    existing_tags = _run(["git", "tag", "--list"], cwd=ROOT).splitlines()
    if tag in existing_tags:
        print(f"Tag {tag} already exists.", file=sys.stderr)
        sys.exit(1)

    _check_clean()

    print(f"Bumping to {version}…")
    _bump_manifest(version)
    _bump_const(version)
    _bump_js(version)

    _run(["git", "add",
          "custom_components/stockpile/manifest.json",
          "custom_components/stockpile/const.py",
          "custom_components/stockpile/frontend/stockpile-card.js",
          ], cwd=ROOT)
    _run(["git", "commit", "-m", tag], cwd=ROOT)
    _run(["git", "tag", "-a", tag, "-m", tag], cwd=ROOT)
    _run(["git", "push", "origin", "main"], cwd=ROOT)
    _run(["git", "push", "origin", tag], cwd=ROOT)

    print(f"Creating GitHub release {tag}…")
    _run(["gh", "release", "create", tag,
          "--title", tag,
          "--notes", description,
          ], cwd=ROOT)

    print(f"Done. {tag} released.")


if __name__ == "__main__":
    main()
