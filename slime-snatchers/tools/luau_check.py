#!/usr/bin/env python3
"""Luau static checker for this project (no external dependencies).

What it does, per .luau file:
  1. Full Luau syntax parse (tokenizer + recursive-descent parser, incl. type
     annotations, string interpolation, compound assignment, if-expressions).
  2. Scope analysis: undefined globals, writes to undeclared globals, unused
     locals (warning), duplicate local in same scope (warning).
  3. Roblox API checks against the luau-lsp type definitions file
     (roblox-types.d.luau): Instance.new class names, GetService names,
     Enum.X.Y items, property writes / method calls on variables whose class
     is known, and keys of `New("Class", { ... })` UI helper calls.

Usage:  python3 tools/luau_check.py src [--defs tools/roblox-types.d.luau]
Exit code is non-zero when any error is found.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "if", "in", "local", "nil", "not", "or", "repeat", "return", "then", "true",
    "until", "while",
}

SYMBOLS = [
    "...", "//=", "..=",
    "==", "~=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "^=", "//", "..", "::", "->",
    "+", "-", "*", "/", "%", "^", "#", "<", ">", "=", "(", ")", "{", "}", "[", "]",
    ";", ":", ",", ".", "?", "|", "&", "@",
]


class LuauSyntaxError(Exception):
    def __init__(self, msg, line):
        super().__init__(f"line {line}: {msg}")
        self.line = line


@dataclass
class Tok:
    kind: str  # name, kw, num, str, interp, sym, eof
    value: object
    line: int


def _read_long_bracket(src, i):
    """src[i] == '['. Returns (level, start_of_content) or None."""
    j = i + 1
    level = 0
    while j < len(src) and src[j] == "=":
        level += 1
        j += 1
    if j < len(src) and src[j] == "[":
        return level, j + 1
    return None


def tokenize(src: str):
    toks = []
    i = 0
    n = len(src)
    line = 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        # comments
        if src.startswith("--", i):
            j = i + 2
            if j < n and src[j] == "[":
                lb = _read_long_bracket(src, j)
                if lb:
                    level, start = lb
                    close = "]" + "=" * level + "]"
                    end = src.find(close, start)
                    if end < 0:
                        raise LuauSyntaxError("unfinished long comment", line)
                    line += src.count("\n", i, end)
                    i = end + len(close)
                    continue
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        # long strings
        if c == "[":
            lb = _read_long_bracket(src, i)
            if lb:
                level, start = lb
                close = "]" + "=" * level + "]"
                end = src.find(close, start)
                if end < 0:
                    raise LuauSyntaxError("unfinished long string", line)
                content = src[start:end]
                if content.startswith("\r\n"):
                    content = content[2:]
                elif content.startswith("\n"):
                    content = content[1:]
                toks.append(Tok("str", content, line))
                line += src.count("\n", i, end)
                i = end + len(close)
                continue
        # quoted strings
        if c in "\"'":
            val, i2, nl = _read_quoted(src, i, line)
            toks.append(Tok("str", val, line))
            line += nl
            i = i2
            continue
        # interpolated strings
        if c == "`":
            parts, exprs, i2, nl = _read_interp(src, i, line)
            toks.append(Tok("interp", (parts, exprs), line))
            line += nl
            i = i2
            continue
        # numbers
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            m = re.compile(
                r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?"
            ).match(src, i)
            if not m:
                raise LuauSyntaxError("bad number", line)
            text = m.group(0)
            j = m.end()
            if j < n and (src[j].isalnum() or src[j] == "_"):
                raise LuauSyntaxError(f"malformed number near '{text}{src[j]}'", line)
            t = text.replace("_", "")
            try:
                if t[:2] in ("0x", "0X"):
                    v = int(t, 16)
                elif t[:2] in ("0b", "0B"):
                    v = int(t[2:], 2)
                else:
                    v = float(t)
            except ValueError:
                raise LuauSyntaxError(f"malformed number '{text}'", line)
            toks.append(Tok("num", v, line))
            i = j
            continue
        # names
        if c.isalpha() or c == "_":
            m = re.compile(r"[A-Za-z_][A-Za-z_0-9]*").match(src, i)
            word = m.group(0)
            toks.append(Tok("kw" if word in KEYWORDS else "name", word, line))
            i = m.end()
            continue
        # symbols
        for s in SYMBOLS:
            if src.startswith(s, i):
                toks.append(Tok("sym", s, line))
                i += len(s)
                break
        else:
            raise LuauSyntaxError(f"unexpected character {c!r}", line)
    toks.append(Tok("eof", None, line))
    return toks


ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
           "\\": "\\", '"': '"', "'": "'", "\n": "\n", "`": "`", "{": "{", "}": "}"}


def _read_escape(src, i, line):
    """src[i] is the char after backslash. Returns (text, new_i, newlines)."""
    c = src[i]
    if c in ESCAPES:
        return ESCAPES[c], i + 1, (1 if c == "\n" else 0)
    if c == "\r":
        j = i + 1
        if j < len(src) and src[j] == "\n":
            j += 1
        return "\n", j, 1
    if c.isdigit():
        m = re.compile(r"\d{1,3}").match(src, i)
        v = int(m.group(0))
        if v > 255:
            raise LuauSyntaxError("decimal escape too large", line)
        return chr(v), m.end(), 0
    if c == "x":
        m = re.compile(r"[0-9a-fA-F]{2}").match(src, i + 1)
        if not m:
            raise LuauSyntaxError("bad \\x escape", line)
        return chr(int(m.group(0), 16)), m.end(), 0
    if c == "u":
        m = re.compile(r"\{([0-9a-fA-F]+)\}").match(src, i + 1)
        if not m:
            raise LuauSyntaxError("bad \\u escape", line)
        return chr(int(m.group(1), 16)), m.end(), 0
    if c == "z":
        j = i + 1
        nl = 0
        while j < len(src) and src[j] in " \t\r\n":
            if src[j] == "\n":
                nl += 1
            j += 1
        return "", j, nl
    raise LuauSyntaxError(f"invalid escape '\\{c}'", line)


def _read_quoted(src, i, line):
    q = src[i]
    j = i + 1
    out = []
    nl = 0
    while True:
        if j >= len(src):
            raise LuauSyntaxError("unfinished string", line)
        c = src[j]
        if c == q:
            return "".join(out), j + 1, nl
        if c == "\n":
            raise LuauSyntaxError("unfinished string (newline)", line)
        if c == "\\":
            t, j, k = _read_escape(src, j + 1, line + nl)
            out.append(t)
            nl += k
            continue
        out.append(c)
        j += 1


def _read_interp(src, i, line):
    """Reads `...{expr}...`; returns (parts, expr_sources, new_i, newlines)."""
    j = i + 1
    parts = []
    exprs = []
    buf = []
    nl = 0
    while True:
        if j >= len(src):
            raise LuauSyntaxError("unfinished interpolated string", line)
        c = src[j]
        if c == "`":
            parts.append("".join(buf))
            return parts, exprs, j + 1, nl
        if c == "\n":
            raise LuauSyntaxError("unfinished interpolated string (newline)", line)
        if c == "\\":
            t, j, k = _read_escape(src, j + 1, line + nl)
            buf.append(t)
            nl += k
            continue
        if c == "{":
            if j + 1 < len(src) and src[j + 1] == "{":
                raise LuauSyntaxError("double brace in interpolated string", line)
            parts.append("".join(buf))
            buf = []
            depth = 1
            k = j + 1
            start = k
            while k < len(src) and depth > 0:
                ch = src[k]
                if ch in "\"'":
                    _, k, _ = _read_quoted(src, k, line)
                    continue
                if ch == "`":
                    _, _, k, _ = _read_interp(src, k, line)
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif ch == "\n":
                    nl += 1
                k += 1
            if depth != 0:
                raise LuauSyntaxError("unterminated {expr} in interpolated string", line)
            exprs.append((src[start:k], line + nl))
            j = k + 1
            continue
        buf.append(c)
        j += 1


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

class Node:
    __slots__ = ("line",)


def node(kind, line, **kw):
    d = {"k": kind, "line": line}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

BINARY_PRIORITY = {
    "or": (1, 1), "and": (2, 2),
    "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
    "..": (5, 4),  # right assoc
    "+": (6, 6), "-": (6, 6),
    "*": (7, 7), "/": (7, 7), "//": (7, 7), "%": (7, 7),
    "^": (10, 9),  # right assoc
}
UNARY_PRIORITY = 8
COMPOUND = {"+=", "-=", "*=", "/=", "//=", "%=", "^=", "..="}


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.p = 0

    # -- helpers --------------------------------------------------------
    @property
    def t(self):
        return self.toks[self.p]

    def peek(self, k=1):
        return self.toks[min(self.p + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.p]
        self.p += 1
        return t

    def is_sym(self, s, tok=None):
        tok = tok or self.t
        return tok.kind == "sym" and tok.value == s

    def is_kw(self, s, tok=None):
        tok = tok or self.t
        return tok.kind == "kw" and tok.value == s

    def accept_sym(self, s):
        if self.is_sym(s):
            return self.next()
        return None

    def accept_kw(self, s):
        if self.is_kw(s):
            return self.next()
        return None

    def expect_sym(self, s, ctx=""):
        if not self.is_sym(s):
            self.error(f"expected '{s}'{ctx}, got {self.desc(self.t)}")
        return self.next()

    def expect_kw(self, s, ctx=""):
        if not self.is_kw(s):
            self.error(f"expected '{s}'{ctx}, got {self.desc(self.t)}")
        return self.next()

    def expect_name(self):
        if self.t.kind != "name":
            self.error(f"expected identifier, got {self.desc(self.t)}")
        return self.next()

    def desc(self, t):
        if t.kind == "eof":
            return "<eof>"
        return repr(t.value) if t.kind in ("str", "num") else f"'{t.value}'"

    def error(self, msg, line=None):
        raise LuauSyntaxError(msg, line if line is not None else self.t.line)

    # -- blocks -------------------------------------------------------
    def parse_chunk(self):
        body = self.block()
        if self.t.kind != "eof":
            self.error(f"unexpected {self.desc(self.t)} (missing 'end' / extra token?)")
        return body

    def block_follow(self):
        t = self.t
        if t.kind == "eof":
            return True
        return t.kind == "kw" and t.value in ("else", "elseif", "end", "until")

    def block(self):
        stmts = []
        while not self.block_follow():
            if self.accept_sym(";"):
                continue
            if self.is_kw("return"):
                stmts.append(self.return_stat())
                break
            st = self.statement()
            if st is not None:
                stmts.append(st)
            self.accept_sym(";")
        return node("block", self.t.line, body=stmts)

    def return_stat(self):
        line = self.next().line
        exprs = []
        if not self.block_follow() and not self.is_sym(";"):
            exprs = self.exprlist()
        self.accept_sym(";")
        return node("return", line, exprs=exprs)

    # -- statements -----------------------------------------------------
    def statement(self):
        t = self.t
        line = t.line
        if t.kind == "sym" and t.value == "@":
            self.attribute()
            return self.statement()
        if t.kind == "kw":
            v = t.value
            if v == "if":
                return self.if_stat()
            if v == "while":
                self.next()
                cond = self.expr()
                self.expect_kw("do", " after while condition")
                body = self.block()
                self.expect_kw("end", f" to close 'while' at line {line}")
                return node("while", line, cond=cond, body=body)
            if v == "do":
                self.next()
                body = self.block()
                self.expect_kw("end", f" to close 'do' at line {line}")
                return node("do", line, body=body)
            if v == "for":
                return self.for_stat()
            if v == "repeat":
                self.next()
                body = self.block()
                self.expect_kw("until", f" to close 'repeat' at line {line}")
                cond = self.expr()
                return node("repeat", line, body=body, cond=cond)
            if v == "function":
                self.next()
                target, is_method = self.funcname()
                func = self.funcbody(line, is_method)
                return node("funcstat", line, target=target, func=func, method=is_method)
            if v == "local":
                self.next()
                if self.accept_kw("function"):
                    name = self.expect_name()
                    func = self.funcbody(line, False)
                    return node("localfunc", line, name=name.value, func=func, nameline=name.line)
                return self.local_stat(line)
            if v == "break":
                self.next()
                return node("break", line)
            self.error(f"unexpected keyword '{v}'")
        if t.kind == "name":
            if t.value == "continue" and self._continue_stmt():
                self.next()
                return node("continue", line)
            if t.value in ("type", "export") and self._type_stmt():
                return self.type_stat()
        return self.expr_stat()

    def attribute(self):
        self.expect_sym("@")
        if self.accept_sym("["):
            depth = 1
            while depth and self.t.kind != "eof":
                if self.is_sym("["):
                    depth += 1
                elif self.is_sym("]"):
                    depth -= 1
                self.next()
        else:
            self.expect_name()

    def _continue_stmt(self):
        nt = self.peek()
        if nt.kind == "eof":
            return True
        if nt.kind == "kw" and nt.value in ("end", "else", "elseif", "until", "local", "if",
                                             "return", "for", "while", "do", "repeat",
                                             "function", "break"):
            return True
        if nt.kind == "sym" and nt.value == ";":
            return True
        if nt.kind == "name" and nt.line != self.t.line:
            return True
        return False

    def _type_stmt(self):
        t = self.t
        nt = self.peek()
        if t.value == "export":
            return nt.kind == "name" and nt.value == "type"
        # `type Name =` or `type Name<`
        if nt.kind == "name":
            nn = self.peek(2)
            return nn.kind == "sym" and nn.value in ("=", "<")
        if nt.kind == "name" and nt.value == "function":
            return True
        return False

    def type_stat(self):
        line = self.t.line
        if self.t.value == "export":
            self.next()
        self.next()  # 'type'
        if self.is_kw("function"):
            self.next()
            self.expect_name()
            self.funcbody(line, False)
            return None
        self.expect_name()
        if self.accept_sym("<"):
            self.generic_params_rest()
        self.expect_sym("=", " in type alias")
        self.type_expr()
        return None

    def generic_params_rest(self):
        # after '<'
        while True:
            if self.accept_sym("..."):
                pass
            else:
                self.expect_name()
                self.accept_sym("...")
                if self.accept_sym("="):
                    self.type_expr()
            if self.accept_sym(","):
                continue
            self.expect_sym(">", " to close generic parameter list")
            return

    def if_stat(self):
        line = self.next().line
        clauses = []
        cond = self.expr()
        self.expect_kw("then", " after if condition")
        body = self.block()
        clauses.append((cond, body))
        orelse = None
        while True:
            if self.is_kw("elseif"):
                self.next()
                c = self.expr()
                self.expect_kw("then", " after elseif condition")
                b = self.block()
                clauses.append((c, b))
                continue
            if self.accept_kw("else"):
                orelse = self.block()
            self.expect_kw("end", f" to close 'if' at line {line}")
            break
        return node("if", line, clauses=clauses, orelse=orelse)

    def for_stat(self):
        line = self.next().line
        first = self.expect_name()
        if self.accept_sym(":"):
            self.type_expr()
        if self.accept_sym("="):
            start = self.expr()
            self.expect_sym(",", " in numeric for")
            stop = self.expr()
            step = None
            if self.accept_sym(","):
                step = self.expr()
            self.expect_kw("do", " in numeric for")
            body = self.block()
            self.expect_kw("end", f" to close 'for' at line {line}")
            return node("fornum", line, var=first.value, start=start, stop=stop, step=step, body=body)
        names = [first.value]
        while self.accept_sym(","):
            names.append(self.expect_name().value)
            if self.accept_sym(":"):
                self.type_expr()
        self.expect_kw("in", " in generic for")
        exprs = self.exprlist()
        self.expect_kw("do", " in generic for")
        body = self.block()
        self.expect_kw("end", f" to close 'for' at line {line}")
        return node("forin", line, names=names, exprs=exprs, body=body)

    def local_stat(self, line):
        names = []
        while True:
            nm = self.expect_name()
            if self.accept_sym("<"):
                # attribute like <const> (Luau supports <const> in newer versions)
                self.expect_name()
                self.expect_sym(">")
            if self.accept_sym(":"):
                self.type_expr()
            names.append((nm.value, nm.line))
            if not self.accept_sym(","):
                break
        exprs = []
        if self.accept_sym("="):
            exprs = self.exprlist()
        return node("local", line, names=names, exprs=exprs)

    def funcname(self):
        nm = self.expect_name()
        target = node("name", nm.line, name=nm.value)
        is_method = False
        while self.is_sym(".") or self.is_sym(":"):
            sep = self.next().value
            key = self.expect_name()
            target = node("member", key.line, obj=target, name=key.value)
            if sep == ":":
                is_method = True
                break
        return target, is_method

    def funcbody(self, line, is_method):
        if self.accept_sym("<"):
            self.generic_params_rest()
        self.expect_sym("(", " to start parameter list")
        params = []
        vararg = False
        if not self.is_sym(")"):
            while True:
                if self.accept_sym("..."):
                    vararg = True
                    if self.accept_sym(":"):
                        self.type_pack_or_type()
                    break
                nm = self.expect_name()
                if self.accept_sym(":"):
                    self.type_expr()
                params.append((nm.value, nm.line))
                if not self.accept_sym(","):
                    break
        self.expect_sym(")", " to close parameter list")
        if self.accept_sym(":"):
            self.return_type()
        body = self.block()
        self.expect_kw("end", f" to close 'function' at line {line}")
        if is_method:
            params = [("self", line)] + params
        return node("function", line, params=params, vararg=vararg, body=body)

    def expr_stat(self):
        line = self.t.line
        e = self.suffixed_expr()
        if self.is_sym("=") or self.is_sym(","):
            targets = [e]
            while self.accept_sym(","):
                targets.append(self.suffixed_expr())
            self.expect_sym("=", " in assignment")
            exprs = self.exprlist()
            for tg in targets:
                if tg["k"] not in ("name", "member", "index"):
                    self.error("cannot assign to this expression", line)
            return node("assign", line, targets=targets, exprs=exprs)
        if self.t.kind == "sym" and self.t.value in COMPOUND:
            op = self.next().value
            val = self.expr()
            if e["k"] not in ("name", "member", "index"):
                self.error("cannot assign to this expression", line)
            return node("compound", line, op=op, target=e, expr=val)
        if e["k"] not in ("call", "methodcall"):
            self.error("syntax error: expression is not a statement (missing '=' or call?)", line)
        return node("callstat", line, call=e)

    # -- expressions ----------------------------------------------------
    def exprlist(self):
        exprs = [self.expr()]
        while self.accept_sym(","):
            exprs.append(self.expr())
        return exprs

    def expr(self, limit=0):
        t = self.t
        if (t.kind == "kw" and t.value == "not") or (t.kind == "sym" and t.value in ("-", "#")):
            op = self.next().value
            operand = self.expr(UNARY_PRIORITY)
            left = node("unop", t.line, op=op, operand=operand)
        else:
            left = self.simple_expr()
        while True:
            t = self.t
            op = t.value if t.kind in ("sym", "kw") else None
            if op in BINARY_PRIORITY and BINARY_PRIORITY[op][0] > limit:
                self.next()
                right = self.expr(BINARY_PRIORITY[op][1])
                left = node("binop", t.line, op=op, left=left, right=right)
                continue
            break
        return left

    def simple_expr(self):
        t = self.t
        line = t.line
        if t.kind == "num":
            self.next()
            e = node("number", line, value=t.value)
        elif t.kind == "str":
            self.next()
            e = node("string", line, value=t.value)
        elif t.kind == "interp":
            self.next()
            parts, srcs = t.value
            exprs = []
            for src, ln in srcs:
                sub = Parser(tokenize(src))
                # make line numbers absolute
                for tk in sub.toks:
                    tk.line += ln - 1
                ex = sub.expr()
                if sub.t.kind != "eof":
                    sub.error("bad expression inside interpolated string")
                exprs.append(ex)
            e = node("interp", line, parts=parts, exprs=exprs)
        elif t.kind == "kw" and t.value == "nil":
            self.next()
            e = node("nil", line)
        elif t.kind == "kw" and t.value in ("true", "false"):
            self.next()
            e = node("bool", line, value=(t.value == "true"))
        elif t.kind == "sym" and t.value == "...":
            self.next()
            e = node("vararg", line)
        elif t.kind == "sym" and t.value == "{":
            e = self.table()
        elif t.kind == "kw" and t.value == "function":
            self.next()
            e = self.funcbody(line, False)
        elif t.kind == "kw" and t.value == "if":
            e = self.if_expr()
        elif t.kind == "sym" and t.value == "@":
            self.attribute()
            return self.simple_expr()
        else:
            e = self.suffixed_expr()
        # type assertion
        while self.is_sym("::"):
            self.next()
            self.type_expr()
        return e

    def if_expr(self):
        line = self.next().line
        clauses = []
        cond = self.expr()
        self.expect_kw("then", " in if-expression")
        val = self.expr()
        clauses.append((cond, val))
        while self.accept_kw("elseif"):
            c = self.expr()
            self.expect_kw("then", " in if-expression")
            clauses.append((c, self.expr()))
        self.expect_kw("else", " (if-expressions require an else branch)")
        other = self.expr()
        return node("ifexpr", line, clauses=clauses, orelse=other)

    def primary_expr(self):
        t = self.t
        if t.kind == "name":
            self.next()
            return node("name", t.line, name=t.value)
        if self.is_sym("("):
            self.next()
            e = self.expr()
            self.expect_sym(")", " to close parenthesis")
            return node("paren", t.line, expr=e)
        self.error(f"unexpected {self.desc(t)}")

    def suffixed_expr(self):
        e = self.primary_expr()
        while True:
            t = self.t
            if self.is_sym("."):
                self.next()
                nm = self.expect_name()
                e = node("member", nm.line, obj=e, name=nm.value)
            elif self.is_sym("["):
                self.next()
                k = self.expr()
                self.expect_sym("]")
                e = node("index", t.line, obj=e, key=k)
            elif self.is_sym(":"):
                self.next()
                nm = self.expect_name()
                args = self.call_args()
                e = node("methodcall", nm.line, obj=e, name=nm.value, args=args)
            elif self.is_sym("(") or self.is_sym("{") or t.kind in ("str", "interp"):
                if self.is_sym("(") and t.line != self.toks[self.p - 1].line:
                    # Lua ambiguity: call on a new line. Luau accepts it but warns.
                    pass
                args = self.call_args()
                e = node("call", t.line, func=e, args=args)
            elif self.is_sym("<") and self._explicit_type_args():
                pass
            else:
                return e

    def _explicit_type_args(self):
        # f<<T>>(...) explicit type instantiation; rare. Not supported intentionally.
        return False

    def call_args(self):
        t = self.t
        if t.kind == "str":
            self.next()
            return [node("string", t.line, value=t.value)]
        if t.kind == "interp":
            return [self.simple_expr()]
        if self.is_sym("{"):
            return [self.table()]
        self.expect_sym("(", " for call arguments")
        args = []
        if not self.is_sym(")"):
            args = self.exprlist()
        self.expect_sym(")", " to close call arguments")
        return args

    def table(self):
        line = self.expect_sym("{").line
        items = []
        while not self.is_sym("}"):
            if self.is_sym("["):
                self.next()
                k = self.expr()
                self.expect_sym("]")
                self.expect_sym("=", " in table constructor")
                items.append(("expr", k, self.expr()))
            elif self.t.kind == "name" and self.is_sym("=", self.peek()):
                nm = self.next()
                self.next()
                items.append(("named", nm.value, self.expr(), nm.line))
            else:
                items.append(("pos", None, self.expr()))
            if not (self.accept_sym(",") or self.accept_sym(";")):
                break
        self.expect_sym("}", f" to close table constructor at line {line}")
        return node("table", line, items=items)

    # -- types ------------------------------------------------------------
    def return_type(self):
        self.type_pack_or_type()

    def type_pack_or_type(self):
        if self.is_sym("(") :
            # could be a type pack (A, B) or parenthesized type or a function type
            self.type_expr()
            return
        if self.accept_sym("..."):
            self.type_expr()
            return
        self.type_expr()

    def type_expr(self):
        # leading | or & allowed
        if self.is_sym("|") or self.is_sym("&"):
            self.next()
        self.simple_type_with_suffix()
        while self.is_sym("|") or self.is_sym("&"):
            self.next()
            self.simple_type_with_suffix()

    def simple_type_with_suffix(self):
        self.simple_type()
        while self.is_sym("?"):
            self.next()

    def simple_type(self):
        t = self.t
        if t.kind == "kw" and t.value in ("nil", "true", "false"):
            self.next()
            return
        if t.kind == "str":
            self.next()
            return
        if t.kind == "name":
            if t.value == "typeof" and self.is_sym("(", self.peek()):
                self.next()
                self.next()
                self.expr()
                self.expect_sym(")")
                return
            self.next()
            if self.is_sym("."):
                self.next()
                self.expect_name()
            if self.is_sym("<"):
                self.next()
                self.type_args_rest()
            if self.accept_sym("..."):
                pass
            return
        if t.kind == "sym" and t.value == "{":
            self.table_type()
            return
        if t.kind == "sym" and t.value == "(":
            # function type or parenthesized type / pack
            self.next()
            if not self.is_sym(")"):
                while True:
                    if self.accept_sym("..."):
                        self.type_expr()
                    else:
                        # named param `name: T`
                        if self.t.kind == "name" and self.is_sym(":", self.peek()):
                            self.next()
                            self.next()
                        self.type_expr()
                    if not self.accept_sym(","):
                        break
            self.expect_sym(")", " in type")
            if self.accept_sym("->"):
                self.type_pack_or_type()
            return
        if t.kind == "sym" and t.value == "<":
            # generic function type <T>(...) -> ...
            self.next()
            self.generic_params_rest()
            self.simple_type()
            return
        if t.kind == "sym" and t.value == "...":
            self.next()
            self.type_expr()
            return
        if t.kind == "kw" and t.value == "function":
            self.error("'function' is not a type; use (args) -> ret")
        self.error(f"expected type, got {self.desc(t)}")

    def type_args_rest(self):
        while True:
            if self.is_sym(">"):
                break
            if self.is_sym("("):
                self.simple_type()
            elif self.accept_sym("..."):
                self.type_expr()
            else:
                self.type_expr()
            if not self.accept_sym(","):
                break
        self.expect_sym(">", " to close type arguments")

    def table_type(self):
        self.expect_sym("{")
        if self.is_sym("}"):
            self.next()
            return
        # array shorthand {T}
        save = self.p
        is_prop = (self.t.kind == "name" and self.is_sym(":", self.peek())) or self.is_sym("[") or (
            self.t.kind == "name" and self.t.value in ("read", "write") and self.peek().kind == "name")
        if not is_prop:
            self.type_expr()
            self.expect_sym("}", " to close array type")
            return
        self.p = save
        while not self.is_sym("}"):
            if self.t.kind == "name" and self.t.value in ("read", "write") and self.peek().kind == "name":
                self.next()
            if self.accept_sym("["):
                if self.t.kind == "str":
                    self.next()
                else:
                    self.type_expr()
                self.expect_sym("]")
                self.expect_sym(":")
                self.type_expr()
            else:
                self.expect_name()
                self.expect_sym(":", " in table type")
                self.type_expr()
            if not (self.accept_sym(",") or self.accept_sym(";")):
                break
        self.expect_sym("}", " to close table type")


def parse(src):
    return Parser(tokenize(src)).parse_chunk()


# ---------------------------------------------------------------------------
# Roblox API database (from luau-lsp definitions)
# ---------------------------------------------------------------------------

LUAU_BUILTINS = {
    "assert", "error", "getmetatable", "ipairs", "next", "pairs", "pcall", "print",
    "rawequal", "rawget", "rawlen", "rawset", "select", "setmetatable", "tonumber",
    "tostring", "type", "typeof", "unpack", "xpcall", "require", "newproxy", "gcinfo",
    "math", "string", "table", "coroutine", "utf8", "bit32", "buffer", "os", "debug",
    "vector", "_G", "_VERSION", "getfenv", "setfenv",
}
DEPRECATED_GLOBALS = {"wait": "task.wait", "spawn": "task.spawn", "delay": "task.delay",
                      "Wait": "task.wait", "Delay": "task.delay", "ElapsedTime": "time()",
                      "elapsedTime": "time()", "unpack": "table.unpack",
                      "getfenv": None, "setfenv": None, "loadstring": None}


class RobloxDB:
    def __init__(self, path):
        src = open(path, encoding="utf-8").read()
        meta = re.match(r"--#METADATA#(\{.*\})", src)
        md = json.loads(meta.group(1)) if meta else {}
        self.creatable = set(md.get("CREATABLE_INSTANCES", []))
        self.services = set(md.get("SERVICES", []))
        self.globals = set(re.findall(r"^declare (?:function )?([A-Za-z_][A-Za-z_0-9]*)", src, re.M))
        self.globals.discard("extern")
        self.classes = {}
        self.dep_methods = {}
        self.live_methods = set()
        for m in re.finditer(r"^declare extern type ([A-Za-z_0-9]+)(?: extends ([A-Za-z_0-9]+))? with\n(.*?)^end",
                             src, re.M | re.S):
            name, parent, body = m.group(1), m.group(2), m.group(3)
            members = set()
            props = set()
            methods = set()
            pending_dep = None
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("@deprecated") or line.startswith("@[deprecated"):
                    use = re.search(r'use = "([^"]+)"', line)
                    pending_dep = use.group(1) if use else ""
                    continue
                fm = re.match(r"function ([A-Za-z_0-9]+)\(", line)
                if fm:
                    methods.add(fm.group(1))
                    members.add(fm.group(1))
                    if pending_dep is not None:
                        self.dep_methods.setdefault(fm.group(1), {})[name] = pending_dep
                    else:
                        self.live_methods.add(fm.group(1))
                    pending_dep = None
                    continue
                pending_dep = None
                pm = re.match(r"([A-Za-z_0-9]+)\s*:", line)
                if pm:
                    props.add(pm.group(1))
                    members.add(pm.group(1))
            self.classes[name] = (parent, members, props, methods)
        # enums
        self.enums = {}
        for m in re.finditer(r"^declare extern type Enum([A-Za-z_0-9]+)_INTERNAL extends Enum with\n(.*?)^end",
                             src, re.M | re.S):
            items = set(re.findall(r"^\s*([A-Za-z_0-9]+): Enum", m.group(2), re.M))
            self.enums[m.group(1)] = items

    def has_class(self, cls):
        return cls in self.classes

    def _walk(self, cls):
        seen = 0
        while cls and cls in self.classes and seen < 50:
            yield self.classes[cls]
            cls = self.classes[cls][0]
            seen += 1

    def has_member(self, cls, name):
        return any(name in c[1] for c in self._walk(cls))

    def has_prop(self, cls, name):
        return any(name in c[2] for c in self._walk(cls))

    def has_method(self, cls, name):
        return any(name in c[3] for c in self._walk(cls))


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

@dataclass
class Var:
    name: str
    line: int
    kind: str  # local, param, func, for
    used: bool = False
    cls: str | None = None
    assigned_after: bool = False


class Scope:
    def __init__(self, parent=None, is_func=False):
        self.parent = parent
        self.vars: dict[str, Var] = {}
        self.is_func = is_func

    def lookup(self, name):
        s = self
        while s:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        return None


class Analyzer:
    def __init__(self, db: RobloxDB | None, filename, ui_helpers=("New",)):
        self.db = db
        self.file = filename
        self.errors = []
        self.warnings = []
        self.ui_helpers = set(ui_helpers)
        self.scope = Scope(is_func=True)
        self.all_vars = []
        self.known_globals = set(LUAU_BUILTINS) | (db.globals if db else set())

    def err(self, line, msg):
        self.errors.append(f"{self.file}:{line}: error: {msg}")

    def warn(self, line, msg):
        self.warnings.append(f"{self.file}:{line}: warning: {msg}")

    # -- scopes -----------------------------------------------------------
    def push(self, is_func=False):
        self.scope = Scope(self.scope, is_func)

    def pop(self):
        for v in self.scope.vars.values():
            self.all_vars.append(v)
        self.scope = self.scope.parent

    def declare(self, name, line, kind, cls=None):
        if name == "_":
            return
        if name in self.scope.vars and kind == "local":
            prev = self.scope.vars[name]
            if not prev.used and prev.kind == "local":
                self.warn(line, f"local '{name}' redeclared in same scope (previous at line {prev.line} unused)")
        v = Var(name, line, kind, cls=cls)
        self.scope.vars[name] = v
        return v

    # -- walk ---------------------------------------------------------------
    def run(self, chunk):
        self.declare_self_and_globals()
        self.block(chunk, new_scope=False)
        self.pop_final()

    def declare_self_and_globals(self):
        pass

    def pop_final(self):
        for v in self.scope.vars.values():
            self.all_vars.append(v)
        for v in self.all_vars:
            if not v.used and v.kind in ("local", "func") and not v.name.startswith("_"):
                self.warn(v.line, f"unused local '{v.name}'")

    def block(self, b, new_scope=True):
        if new_scope:
            self.push()
        for st in b["body"]:
            self.stat(st)
        if new_scope:
            self.pop()

    def stat(self, s):
        k = s["k"]
        if k == "local":
            for e in s["exprs"]:
                self.expr(e)
            for idx, (nm, ln) in enumerate(s["names"]):
                cls = None
                if idx < len(s["exprs"]) and len(s["names"]) == len(s["exprs"]):
                    cls = self.infer_class(s["exprs"][idx])
                self.declare(nm, ln, "local", cls)
        elif k == "localfunc":
            self.declare(s["name"], s["nameline"], "func")
            self.function(s["func"])
        elif k == "funcstat":
            tg = s["target"]
            if tg["k"] == "name":
                v = self.scope.lookup(tg["name"])
                if v is None:
                    if tg["name"] not in self.known_globals:
                        self.err(s["line"], f"global function '{tg['name']}' (use 'local function')")
                else:
                    v.used = True
            else:
                self.expr(tg)
            self.function(s["func"])
        elif k == "assign":
            for e in s["exprs"]:
                self.expr(e)
            for tg in s["targets"]:
                self.assign_target(tg)
        elif k == "compound":
            self.expr(s["expr"])
            self.expr(s["target"])  # read
            self.assign_target(s["target"])
        elif k == "callstat":
            self.expr(s["call"])
        elif k == "if":
            for cond, body in s["clauses"]:
                self.expr(cond)
                self.block(body)
            if s["orelse"] is not None:
                self.block(s["orelse"])
        elif k == "while":
            self.expr(s["cond"])
            self.block(s["body"])
        elif k == "repeat":
            self.push()
            for st in s["body"]["body"]:
                self.stat(st)
            self.expr(s["cond"])  # until sees the body's locals
            self.pop()
        elif k == "do":
            self.block(s["body"])
        elif k == "fornum":
            self.expr(s["start"])
            self.expr(s["stop"])
            if s["step"] is not None:
                self.expr(s["step"])
            self.push()
            self.declare(s["var"], s["line"], "for")
            self.block(s["body"], new_scope=False)
            self.pop()
        elif k == "forin":
            for e in s["exprs"]:
                self.expr(e)
            self.push()
            for nm in s["names"]:
                self.declare(nm, s["line"], "for")
            self.block(s["body"], new_scope=False)
            self.pop()
        elif k == "return":
            for e in s["exprs"]:
                self.expr(e)
        elif k in ("break", "continue"):
            pass
        else:
            raise RuntimeError("unknown stat " + k)

    def assign_target(self, tg):
        if tg["k"] == "name":
            v = self.scope.lookup(tg["name"])
            if v is None:
                if tg["name"] not in self.known_globals:
                    self.err(tg["line"], f"assignment to undeclared global '{tg['name']}'")
                else:
                    self.err(tg["line"], f"overwriting built-in global '{tg['name']}'")
            else:
                v.assigned_after = True
                v.cls = None
        elif tg["k"] == "member":
            self.expr(tg["obj"])
            self.check_prop_write(tg)
        elif tg["k"] == "index":
            self.expr(tg["obj"])
            self.expr(tg["key"])

    def function(self, f):
        self.push(is_func=True)
        for nm, ln in f["params"]:
            v = self.declare(nm, ln, "param")
        self.block(f["body"], new_scope=False)
        self.pop()

    def expr(self, e):
        k = e["k"]
        if k == "name":
            v = self.scope.lookup(e["name"])
            if v is None:
                nm = e["name"]
                if nm not in self.known_globals:
                    self.err(e["line"], f"unknown global '{nm}'")
                elif nm in DEPRECATED_GLOBALS:
                    rep = DEPRECATED_GLOBALS[nm]
                    self.warn(e["line"], f"deprecated global '{nm}'" + (f" (use {rep})" if rep else ""))
            else:
                v.used = True
        elif k in ("number", "string", "nil", "bool", "vararg"):
            pass
        elif k == "interp":
            for x in e["exprs"]:
                self.expr(x)
        elif k == "function":
            self.function(e)
        elif k == "table":
            for item in e["items"]:
                if item[0] == "expr":
                    self.expr(item[1])
                self.expr(item[2])
        elif k == "binop":
            self.expr(e["left"])
            self.expr(e["right"])
        elif k == "unop":
            self.expr(e["operand"])
        elif k == "paren":
            self.expr(e["expr"])
        elif k == "member":
            self.expr(e["obj"])
            self.check_enum(e)
        elif k == "index":
            self.expr(e["obj"])
            self.expr(e["key"])
        elif k == "call":
            self.expr(e["func"])
            for a in e["args"]:
                self.expr(a)
            self.check_call(e)
        elif k == "methodcall":
            self.expr(e["obj"])
            for a in e["args"]:
                self.expr(a)
            self.check_method(e)
        elif k == "ifexpr":
            for c, v in e["clauses"]:
                self.expr(c)
                self.expr(v)
            self.expr(e["orelse"])
        else:
            raise RuntimeError("unknown expr " + k)

    # -- roblox checks --------------------------------------------------------
    def infer_class(self, e):
        if not self.db:
            return None
        if e["k"] == "call" and e["func"]["k"] == "member" and e["func"]["obj"]["k"] == "name" \
                and e["func"]["obj"]["name"] == "Instance" and e["func"]["name"] == "new" \
                and e["args"] and e["args"][0]["k"] == "string":
            return e["args"][0]["value"]
        if e["k"] == "methodcall" and e["name"] == "GetService" and e["args"] and e["args"][0]["k"] == "string":
            return e["args"][0]["value"]
        if e["k"] == "call" and e["func"]["k"] == "name" and e["func"]["name"] in self.ui_helpers \
                and e["args"] and e["args"][0]["k"] == "string":
            return e["args"][0]["value"]
        if e["k"] == "name" and e["name"] == "workspace":
            return "Workspace"
        return None

    def var_class(self, e):
        if e["k"] == "name":
            v = self.scope.lookup(e["name"])
            if v is not None and not v.assigned_after:
                return v.cls
            if v is None and e["name"] == "workspace":
                return "Workspace"
            if v is None and e["name"] == "game":
                return "DataModel"
        return None

    def check_prop_write(self, tg):
        if not self.db:
            return
        cls = self.var_class(tg["obj"])
        if cls and self.db.has_class(cls) and not self.db.has_prop(cls, tg["name"]):
            self.err(tg["line"], f"'{tg['name']}' is not a property of {cls}")

    def check_method(self, e):
        if not self.db:
            return
        nm = e["name"]
        if nm in ("connect", "disconnect", "wait"):
            self.warn(e["line"], f"lowercase ':{nm}' is deprecated")
        if nm == "GetService" and e["args"] and e["args"][0]["k"] == "string":
            svc = e["args"][0]["value"]
            if svc not in self.db.services:
                self.err(e["line"], f"unknown service '{svc}'")
        if nm in self.db.dep_methods and nm not in self.db.live_methods:
            uses = sorted(set(v for v in self.db.dep_methods[nm].values() if v))
            self.warn(e["line"], f"':{nm}' is deprecated" + (f" (use {', '.join(uses)})" if uses else ""))
        cls = self.var_class(e["obj"])
        if cls and self.db.has_class(cls) and not self.db.has_method(cls, nm):
            if self.db.has_member(cls, nm):
                return  # e.g. callbacks invoked as methods (rare)
            self.err(e["line"], f"'{nm}' is not a method of {cls}")

    def check_call(self, e):
        if not self.db:
            return
        f = e["func"]
        if f["k"] == "member" and f["obj"]["k"] == "name" and f["obj"]["name"] == "Instance" and f["name"] == "new":
            if not self.scope.lookup("Instance") and e["args"] and e["args"][0]["k"] == "string":
                cls = e["args"][0]["value"]
                if cls not in self.db.creatable:
                    self.err(e["line"], f"Instance.new: '{cls}' is not a creatable class")
        if f["k"] == "name" and f["name"] in self.ui_helpers and e["args"] and e["args"][0]["k"] == "string":
            cls = e["args"][0]["value"]
            if cls not in self.db.creatable:
                self.err(e["line"], f"{f['name']}: '{cls}' is not a creatable class")
            elif len(e["args"]) > 1 and e["args"][1]["k"] == "table":
                for item in e["args"][1]["items"]:
                    if item[0] == "named":
                        key = item[1]
                        if key in ("Children", "Parent", "Ref", "OnCreate"):
                            continue
                        if not self.db.has_member(cls, key):
                            self.err(item[3] if len(item) > 3 else e["line"],
                                     f"{f['name']}('{cls}'): '{key}' is not a member of {cls}")

    def check_enum(self, e):
        if not self.db:
            return
        obj = e["obj"]
        if obj["k"] == "member" and obj["obj"]["k"] == "name" and obj["obj"]["name"] == "Enum" \
                and not self.scope.lookup("Enum"):
            enum_name = obj["name"]
            if enum_name not in self.db.enums:
                self.err(e["line"], f"unknown enum 'Enum.{enum_name}'")
            elif e["name"] not in self.db.enums[enum_name] and e["name"] not in (
                    "GetEnumItems", "FromName", "FromValue"):
                self.err(e["line"], f"'{e['name']}' is not an item of Enum.{enum_name}")
        elif obj["k"] == "name" and obj["name"] == "Enum" and not self.scope.lookup("Enum"):
            if e["name"] not in self.db.enums and e["name"] not in ("GetEnums",):
                self.err(e["line"], f"unknown enum 'Enum.{e['name']}'")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def check_file(path, db):
    src = open(path, encoding="utf-8").read()
    rel = os.path.relpath(path)
    try:
        chunk = parse(src)
    except LuauSyntaxError as ex:
        return [f"{rel}:{ex.line}: syntax error: {ex}"], []
    an = Analyzer(db, rel)
    an.run(chunk)
    return list(dict.fromkeys(an.errors)), list(dict.fromkeys(an.warnings))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--defs", default=os.path.join(os.path.dirname(__file__), "roblox-types.d.luau"))
    ap.add_argument("--no-warnings", action="store_true")
    args = ap.parse_args(argv)
    db = RobloxDB(args.defs) if os.path.exists(args.defs) else None
    if db is None:
        print(f"note: definitions file not found at {args.defs}; Roblox API checks disabled")
    files = []
    for p in args.paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                for nm in sorted(names):
                    if nm.endswith((".luau", ".lua")):
                        files.append(os.path.join(root, nm))
        else:
            files.append(p)
    total_e = total_w = 0
    for f in sorted(files):
        errs, warns = check_file(f, db)
        for m in errs:
            print(m)
        if not args.no_warnings:
            for m in warns:
                print(m)
        total_e += len(errs)
        total_w += len(warns)
    print(f"checked {len(files)} files: {total_e} errors, {total_w} warnings")
    return 1 if total_e else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
