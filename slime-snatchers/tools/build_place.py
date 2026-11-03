#!/usr/bin/env python3
"""Builds a Roblox place file (.rbxlx, XML) from a Rojo-style project.

Supports the subset of the Rojo project format this game uses:
  * "$className", "$path", "$properties" (string / bool / number / {"Enum": n})
  * directory  -> Folder (or the script defined by init(.server|.client).luau)
  * *.server.luau -> Script, *.client.luau -> LocalScript, *.luau -> ModuleScript

The output opens directly in Roblox Studio (File > Open from File...).
The same project also works with the real Rojo CLI (`rojo build -o game.rbxlx`).

Usage: python3 tools/build_place.py default.project.json -o build/SlimeSnatchers.rbxlx
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from xml.sax.saxutils import escape

SCRIPT_SUFFIXES = (
    (".server.luau", "Script"),
    (".client.luau", "LocalScript"),
    (".server.lua", "Script"),
    (".client.lua", "LocalScript"),
    (".luau", "ModuleScript"),
    (".lua", "ModuleScript"),
)


def _ref():
    return "RBX" + uuid.uuid4().hex.upper()


def _cdata(text: str) -> str:
    # A CDATA section cannot contain "]]>"; split it across sections.
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


class Inst:
    def __init__(self, cls: str, name: str):
        self.cls = cls
        self.name = name
        self.props: dict[str, tuple[str, object]] = {}
        self.children: list[Inst] = []

    def xml(self, depth=1) -> str:
        pad = "  " * depth
        out = [f'{pad}<Item class="{self.cls}" referent="{_ref()}">', f"{pad}  <Properties>",
               f'{pad}    <string name="Name">{escape(self.name)}</string>']
        for key, (kind, val) in self.props.items():
            if kind == "ProtectedString":
                out.append(f'{pad}    <ProtectedString name="{key}">{_cdata(val)}</ProtectedString>')
            elif kind == "bool":
                out.append(f'{pad}    <bool name="{key}">{"true" if val else "false"}</bool>')
            elif kind == "token":
                out.append(f'{pad}    <token name="{key}">{int(val)}</token>')
            elif kind == "float":
                out.append(f'{pad}    <float name="{key}">{val}</float>')
            elif kind == "string":
                out.append(f'{pad}    <string name="{key}">{escape(str(val))}</string>')
            else:
                raise ValueError(f"unsupported property kind {kind}")
        out.append(f"{pad}  </Properties>")
        for ch in self.children:
            out.append(ch.xml(depth + 1))
        out.append(f"{pad}</Item>")
        return "\n".join(out)


def _script_kind(filename):
    for suffix, cls in SCRIPT_SUFFIXES:
        if filename.endswith(suffix):
            return filename[: -len(suffix)], cls
    return None, None


def from_path(path: str, name: str | None = None) -> Inst:
    if os.path.isdir(path):
        init = None
        for fn in sorted(os.listdir(path)):
            stem, cls = _script_kind(fn)
            if stem == "init":
                init = (fn, cls)
        if init:
            inst = Inst(init[1], name or os.path.basename(path))
            inst.props["Source"] = ("ProtectedString", open(os.path.join(path, init[0]), encoding="utf-8").read())
        else:
            inst = Inst("Folder", name or os.path.basename(path))
        for fn in sorted(os.listdir(path)):
            full = os.path.join(path, fn)
            stem, cls = _script_kind(fn)
            if stem == "init" or fn.startswith(".") or fn.endswith(".meta.json"):
                continue
            if os.path.isdir(full) or cls:
                inst.children.append(from_path(full))
        return inst
    stem, cls = _script_kind(os.path.basename(path))
    if not cls:
        raise ValueError(f"not a script file: {path}")
    inst = Inst(cls, name or stem)
    inst.props["Source"] = ("ProtectedString", open(path, encoding="utf-8").read())
    return inst


def _apply_props(inst: Inst, props: dict):
    for key, val in props.items():
        if isinstance(val, bool):
            inst.props[key] = ("bool", val)
        elif isinstance(val, (int, float)):
            inst.props[key] = ("float", val)
        elif isinstance(val, str):
            inst.props[key] = ("string", val)
        elif isinstance(val, dict) and "Enum" in val:
            inst.props[key] = ("token", val["Enum"])
        else:
            raise ValueError(f"unsupported property value for {key}: {val!r}")


def from_tree(name: str, tree: dict, base: str) -> Inst:
    if "$path" in tree:
        inst = from_path(os.path.join(base, tree["$path"]), name)
        if "$className" in tree:
            inst.cls = tree["$className"]
    else:
        inst = Inst(tree.get("$className", name), name)
    _apply_props(inst, tree.get("$properties", {}))
    for key, sub in tree.items():
        if key.startswith("$"):
            continue
        inst.children.append(from_tree(key, sub, base))
    return inst


def build(project_file: str, out_file: str):
    base = os.path.dirname(os.path.abspath(project_file))
    project = json.load(open(project_file, encoding="utf-8"))
    tree = project["tree"]
    services = []
    for key, sub in tree.items():
        if key.startswith("$"):
            continue
        services.append(from_tree(key, sub, base))
    body = "\n".join(s.xml() for s in services)
    xml = ('<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">\n'
           '  <External>null</External>\n  <External>nil</External>\n'
           f"{body}\n</roblox>\n")
    os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write(xml)
    count = xml.count("<Item ")
    print(f"wrote {out_file} ({count} instances, {len(xml) // 1024} KiB)")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    build(a.project, a.out)
