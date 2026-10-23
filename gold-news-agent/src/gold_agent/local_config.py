"""This computer's own settings, kept out of git so an update never touches them.

`config/local.yaml` overrides `config/settings.yaml` key by key (nested mappings merge, any
other value replaces), and `config/sources.local.yaml` overrides fields of sources by id (the
installer marks the sources it verified there). Both files are git-ignored.

Standalone on purpose (stdlib + PyYAML): the Windows updater runs this file from the NEW
revision before switching to it, so `migrate` can move edits made to the tracked config files
into the local ones and the update itself never conflicts.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

SETTINGS, SOURCES = "settings.yaml", "sources.yaml"
LOCAL_SETTINGS, LOCAL_SOURCES = "local.yaml", "sources.local.yaml"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
HEADER = (
    "# Pengaturan khusus komputer ini: tidak ikut git, jadi aman saat program diperbarui.\n"
    "# Isinya menimpa config/{base}. Hapus sebuah baris untuk kembali ke nilai bawaan.\n"
)
CLOCK_TIME = re.compile(r"^\d{1,2}:\d{2}$")  # YAML 1.1 would read 14:00 as the number 840
_SAME = object()


def merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Nested mappings merge key by key; any other value (lists too) replaces."""
    out = dict(base)
    for key, value in override.items():
        current = out.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            out[key] = merge(current, value)
        else:
            out[key] = value
    return out


def changes(base: Any, current: Any) -> Any:
    """What `current` changed relative to `base` (nested mappings recurse), or _SAME."""
    if isinstance(base, Mapping) and isinstance(current, Mapping):
        out: dict[str, Any] = {}
        for key, value in current.items():  # pyright: ignore[reportUnknownVariableType]
            diff = changes(base.get(key, _SAME), value)
            if diff is not _SAME:
                out[str(key)] = diff
        return out or _SAME
    return _SAME if base == current else current


def apply_sources(registry: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay per-id fields from sources.local.yaml on the registry."""
    entries: list[dict[str, Any]] = list(registry.get("sources") or [])
    unknown = sorted(set(overrides) - {entry.get("id") for entry in entries})
    if unknown:
        raise ValueError(f"{LOCAL_SOURCES}: sumber tidak dikenal: {', '.join(unknown)}")
    return dict(registry) | {
        "sources": [entry | dict(overrides.get(entry["id"]) or {}) for entry in entries]
    }


def source_changes(base: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Per-id fields that differ between two registries (sources only in `current` skipped)."""
    before = {entry["id"]: entry for entry in base.get("sources") or []}
    out: dict[str, Any] = {}
    for entry in current.get("sources") or []:
        old = before.get(entry.get("id"))
        if old is not None:
            diff = {key: value for key, value in entry.items() if old.get(key) != value}
            if diff:
                out[entry["id"]] = diff
    return out


def read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_local(path: Path, data: Mapping[str, Any], base: str) -> None:
    body = yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True)
    path.write_text(HEADER.format(base=base) + body, encoding="utf-8")


def load_merged_settings(path: Path) -> Any:
    """settings.yaml with this computer's local.yaml on top."""
    data = read_yaml(path)
    local = read_yaml(path.with_name(LOCAL_SETTINGS))
    return merge(data, local) if isinstance(data, Mapping) and local else data


def load_merged_sources(path: Path) -> Any:
    data = read_yaml(path)
    local = read_yaml(path.with_name(LOCAL_SOURCES))
    return apply_sources(data, local) if isinstance(data, Mapping) and local else data


# ------------------------------------------------------------------ edits


def parse_value(raw: str) -> Any:
    return raw if CLOCK_TIME.match(raw) else yaml.safe_load(raw)


def set_value(config: Path, dotted: str, raw: str) -> dict[str, Any]:
    """Write one key (e.g. `http.contact_email`) into local.yaml; returns the new content."""
    path = config / LOCAL_SETTINGS
    local: dict[str, Any] = read_yaml(path) or {}
    value: Any = parse_value(raw)
    for key in reversed(dotted.split(".")):
        value = {key: value}
    local = merge(local, value)
    write_local(path, local, SETTINGS)
    return local


def mark_verified(config: Path, ids: Sequence[str]) -> dict[str, Any]:
    """Record sources this computer verified (`status: verified`) in sources.local.yaml."""
    known = {entry["id"] for entry in (read_yaml(config / SOURCES) or {}).get("sources") or []}
    unknown = sorted(set(ids) - known)
    if unknown:
        raise ValueError(f"sumber tidak dikenal: {', '.join(unknown)}")
    path = config / LOCAL_SOURCES
    local: dict[str, Any] = read_yaml(path) or {}
    for source_id in ids:
        local[source_id] = dict(local.get(source_id) or {}) | {"status": "verified"}
    write_local(path, local, SOURCES)
    return local


def _git(config: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise OSError("git tidak ditemukan")
    done = subprocess.run(  # noqa: S603 - fixed git subcommands on this repository
        [git, *args], cwd=config, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout


def migrate(config: Path, backups: Path) -> list[str]:
    """Move edits of the tracked settings.yaml / sources.yaml into the local files, keep the
    full diff as a patch in `backups`, and restore the tracked files so `git pull` is clean."""
    edited = [
        name for name in (SETTINGS, SOURCES) if _git(config, "status", "--porcelain", "--", name)
    ]
    if not edited:
        return []
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).astimezone()
    patch = backups / f"config-sebelum-update-{stamp:%Y%m%d-%H%M%S}.patch"
    patch.write_text(_git(config, "diff", "--", *edited), encoding="utf-8")
    moved: list[str] = []
    for name in edited:
        base = yaml.safe_load(_git(config, "show", f"HEAD:./{name}"))
        current = read_yaml(config / name)
        if name == SETTINGS:
            diff = changes(base, current)
            if diff is not _SAME:
                local = merge(read_yaml(config / LOCAL_SETTINGS) or {}, diff)
                write_local(config / LOCAL_SETTINGS, local, SETTINGS)
                moved += [f"{LOCAL_SETTINGS}: {key}" for key in diff]
        else:
            diff = source_changes(base, current)
            if diff:
                local = merge(read_yaml(config / LOCAL_SOURCES) or {}, diff)
                write_local(config / LOCAL_SOURCES, local, SOURCES)
                moved += [f"{LOCAL_SOURCES}: {key}" for key in diff]
    _git(config, "checkout", "--", *edited)
    return moved


def _drop(data: dict[str, Any], loc: Sequence[Any]) -> bool:
    """Delete the key at `loc` (and parents it leaves empty); False when it is not there."""
    parent: Any = data
    for key in loc[:-1]:
        parent = parent.get(key) if isinstance(parent, dict) else None
    if not isinstance(parent, dict) or loc[-1] not in parent:
        return False
    del parent[loc[-1]]
    if not parent and len(loc) > 1:
        _drop(data, loc[:-1])
    return True


def repair(config: Path, validate: Callable[[], object]) -> list[str]:
    """After an update: drop from local.yaml the settings this version no longer has (the
    loader reports them as `extra_forbidden`), so an old edit cannot stop the agent starting.
    Anything else wrong is raised as is."""
    removed: list[str] = []
    while True:
        try:
            validate()
        except Exception as exc:
            errors = getattr(exc, "errors", None)  # a pydantic ValidationError has errors()
            found = cast(list[dict[str, Any]], errors()) if callable(errors) else []
            locs = [tuple(e["loc"]) for e in found if e.get("type") == "extra_forbidden"]
            local: dict[str, Any] = read_yaml(config / LOCAL_SETTINGS) or {}
            dropped = [loc for loc in locs if _drop(local, loc)]
            if not dropped:
                raise
            write_local(config / LOCAL_SETTINGS, local, SETTINGS)
            removed += [".".join(str(part) for part in loc) for loc in dropped]
            continue
        return removed


# ------------------------------------------------------------------ command line


def _checked(
    config: Path, edit: Callable[[], object], validate: Callable[[], object] | None
) -> None:
    """Apply an edit to the local files; undo it when the resulting config does not load."""
    before = {name: read_text(config / name) for name in (LOCAL_SETTINGS, LOCAL_SOURCES)}
    edit()
    if validate is None:
        return
    try:
        validate()
    except Exception:
        for name, text in before.items():
            if text is None:
                (config / name).unlink(missing_ok=True)
            else:
                (config / name).write_text(text, encoding="utf-8")
        raise


def read_text(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def main(argv: Sequence[str] | None = None, validate: Callable[[], object] | None = None) -> int:
    """`validate` (the app's own config loader) rejects an edit that would stop it starting."""
    parser = argparse.ArgumentParser(prog="gold-agent config", description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    sub = parser.add_subparsers(dest="action", required=True)
    put = sub.add_parser("set", help="tulis satu pengaturan ke config/local.yaml")
    put.add_argument("key", help="mis. http.contact_email")
    put.add_argument("value")
    verified = sub.add_parser("verified", help="tandai sumber terverifikasi di komputer ini")
    verified.add_argument("ids", nargs="+")
    move = sub.add_parser("migrate", help="pindahkan edit config ke file lokal sebelum update")
    move.add_argument("--backups", type=Path, default=None)
    sub.add_parser("repair", help="buang pengaturan lama yang tidak dipakai versi ini")
    args = parser.parse_args(argv)
    config: Path = args.config_dir
    try:
        if args.action == "set":
            _checked(config, lambda: set_value(config, args.key, args.value), validate)
            print(f"{args.key} disimpan di config/{LOCAL_SETTINGS}")
        elif args.action == "verified":
            _checked(config, lambda: mark_verified(config, args.ids), validate)
            print(f"{len(args.ids)} sumber ditandai verified di config/{LOCAL_SOURCES}")
        elif args.action == "repair":
            if validate is None:
                raise ValueError("repair butuh pemuat config program (jalankan lewat gold-agent)")
            removed = repair(config, validate)
            print(
                "\n".join(f"dibuang (tidak dipakai lagi): {key}" for key in removed) or "config OK"
            )
        else:
            backups = args.backups or config.parent / "data" / "backups"
            moved = migrate(config, backups)
            print("\n".join(f"dipindah ke {line}" for line in moved) or "config tidak diubah")
    except (ValueError, OSError, subprocess.CalledProcessError, yaml.YAMLError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else exc
        print(f"GAGAL: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
