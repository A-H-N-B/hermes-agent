#!/usr/bin/env python3
"""Cross-module reference check for this project.

Verifies that every `Alias.member` used through a `require(...)`d module (and
every `S.Service.member` used through the server's service table) is actually
defined by that module (`function M.member` or `M.member = ...`).

Usage: python3 tools/xref_check.py src
"""
from __future__ import annotations

import os
import re
import sys

REQUIRE_RE = re.compile(r'local\s+(\w+)\s*=\s*require\([^\n]*?WaitForChild\("(\w+)"\)\)')
EXPORT_FN_RE = re.compile(r"^function\s+(\w+)\.(\w+)\s*\(", re.M)
EXPORT_ASSIGN_RE = re.compile(r"^(\w+)\.(\w+)\s*=", re.M)
RETURN_RE = re.compile(r"^return\s+(\w+)\s*$", re.M)
SERVICE_USE_RE = re.compile(r"\bS\.(\w+)\.(\w+)")


def strip_comments_and_strings(src: str) -> str:
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", "", src, flags=re.S)
    src = re.sub(r"--[^\n]*", "", src)
    src = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', src)
    src = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", src)
    src = re.sub(r"`(?:\\.|[^`\\])*`", "``", src)
    return src


def module_name(path: str) -> str:
    base = os.path.basename(path)
    if base.startswith("init."):
        return os.path.basename(os.path.dirname(path))
    return base.split(".")[0]


def main(root: str) -> int:
    files = []
    for d, _, names in os.walk(root):
        for n in names:
            if n.endswith(".luau"):
                files.append(os.path.join(d, n))
    exports: dict[str, set[str]] = {}
    returns_table: dict[str, bool] = {}
    sources = {}
    raw_sources = {}
    for f in files:
        raw = open(f, encoding="utf-8").read()
        src = strip_comments_and_strings(raw)
        sources[f] = src
        raw_sources[f] = re.sub(r"--[^\n]*", "", raw)
        name = module_name(f)
        ret = RETURN_RE.findall(src)
        table_name = ret[-1] if ret else None
        members = set()
        if table_name:
            for t, m in EXPORT_FN_RE.findall(src) + EXPORT_ASSIGN_RE.findall(src):
                if t == table_name:
                    members.add(m)
        exports[name] = members
        returns_table[name] = bool(table_name) and bool(members)
    errors = 0
    for f, src in sources.items():
        aliases = {alias: mod for alias, mod in REQUIRE_RE.findall(raw_sources[f])}
        own = module_name(f)
        ret = RETURN_RE.findall(src)
        if ret and returns_table.get(own):
            aliases.setdefault(ret[-1], own)
        for alias, mod in aliases.items():
            if mod not in exports or not returns_table.get(mod):
                continue
            for m in re.finditer(r"(?<![\w.:])" + re.escape(alias) + r"\.(\w+)", src):
                member = m.group(1)
                if member not in exports[mod]:
                    line = src.count("\n", 0, m.start()) + 1
                    print(f"{os.path.relpath(f)}:{line}: {alias}.{member} is not defined in module {mod}")
                    errors += 1
        for svc, member in SERVICE_USE_RE.findall(src):
            if svc not in exports:
                print(f"{os.path.relpath(f)}: unknown service S.{svc}")
                errors += 1
            elif member not in exports[svc]:
                print(f"{os.path.relpath(f)}: S.{svc}.{member} is not defined")
                errors += 1
    print(f"xref: {len(files)} files, {errors} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "src"))
