#!/usr/bin/env python3
"""Parse ABC/ABCX music notation and convert to standard format.

Mirrors the logic in src/abcx.js. Pure Python, stdlib only.

The "standard" format unifies per-voice L: declarations into a single
unit-note-length so downstream renderers (abcjs in particular) don't
mis-attribute durations across voices.

CLI examples:
    python3 scripts/to_standard.py input.abc                  # -> input.std.abcx
    python3 scripts/to_standard.py input.abc -t abc           # -> input.std.abc
    python3 scripts/to_standard.py input.abcx -t abc -o out.abc
    python3 scripts/to_standard.py input.abc --stdout
    cat file.abc | python3 scripts/to_standard.py - -t abcx

Programmatic:
    from to_standard import to_standard_abc, to_standard_abcx, AbcError
    try:
        std = to_standard_abcx(open("foo.abc").read())
    except AbcError as e:
        ...

Errors are raised as AbcError with line/column info so other components
can surface them. Use --no-validate or validate=False to downgrade
structural problems to warnings on stderr.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Errors / diagnostics
# ---------------------------------------------------------------------------

class AbcError(Exception):
    """Raised on hard parse / structural failures."""

    def __init__(self, message: str, line: Optional[int] = None, column: Optional[int] = None) -> None:
        loc = f" (line {line + 1}, col {column})" if line is not None else ""
        super().__init__(message + loc)
        self.line = line
        self.column = column
        self.raw_message = message


@dataclass
class Diagnostic:
    severity: str  # "error" | "warning"
    line: int
    column: int
    message: str


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

FIELD_RE = re.compile(r"^[A-Za-z]:")
L_RE = re.compile(r"^L:\s*(\d+)\s*/\s*(\d+)")
V_FIELD_RE = re.compile(r"^V:\s*(\S+)")
INLINE_V_RE = re.compile(r"^\[V:([^\]]+)\]\s*(.*)$")
INLINE_FIELD_RE = re.compile(r"^[A-Za-z]:")
SCORE_RE = re.compile(r"^\s*%%score\s+", re.MULTILINE)
RANGE_RE = re.compile(
    r"@\[([A-Za-z0-9_.]+):([A-Za-z0-9_-]+):([A-Za-z0-9_-]+)([()])"
)
NOTE_RE = re.compile(r"((?:\^{1,2}|_{1,2}|=)?)([A-Ga-gxyz])([,']*)")
DUR_RE = re.compile(r"(\d+)?(/+)?(\d+)?")
BAR_CHAR_RE = re.compile(r"[:|\]\[]")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def parse_fraction(value: str) -> Fraction:
    m = re.match(r"^(\d+)\s*/\s*(\d+)", str(value or "").strip())
    if not m:
        return Fraction(1, 8)
    return Fraction(int(m.group(1)), int(m.group(2)))


def parse_meter(value: str) -> Fraction:
    t = str(value or "").strip()
    if t in ("C", "C|"):
        return Fraction(1)
    return parse_fraction(t or "4/4")


def strip_comment(line: str) -> str:
    quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            quote = not quote
        elif ch == "%" and not quote:
            return line[:i]
    return line


def split_top_level(source: str, delim: str) -> list:
    """Split by `delim` at top level (ignoring quotes/brackets/braces and @[..] ranges)."""
    parts: list = []
    cur = ""
    quote = False
    bracket = 0
    brace = 0
    i = 0
    while i < len(source):
        m = RANGE_RE.match(source, i)
        if not quote and m:
            cur += m.group(0)
            i = m.end()
            continue
        ch = source[i]
        if ch == '"':
            quote = not quote
        if not quote:
            if ch == "[":
                bracket += 1
            elif ch == "]" and bracket:
                bracket -= 1
            elif ch == "{":
                brace += 1
            elif ch == "}" and brace:
                brace -= 1
        if ch == delim and not quote and bracket == 0 and brace == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


def normalize_voice_name(name: str) -> str:
    m = re.match(r"^v?(\d+)$", str(name).strip(), re.IGNORECASE)
    return f"V{m.group(1)}" if m else str(name).strip()


def strip_leading_v(name: str) -> str:
    return re.sub(r"^V(\d)", r"\1", str(name))


def parse_score_voices(score_line: str) -> list:
    voices: list = []
    seen: set = set()
    for m in re.finditer(r"\(([^)]*)\)", score_line or ""):
        for name in m.group(1).strip().split():
            norm = normalize_voice_name(name)
            if norm and norm not in seen:
                seen.add(norm)
                voices.append(norm)
    return voices


def is_abcx(source: str) -> bool:
    """Lenient: %%score directive, ; in a music line, or @[..] range markers."""
    s = source or ""
    if SCORE_RE.search(s):
        return True
    for line in s.split("\n"):
        ls = line.lstrip()
        if not ls or ls.startswith("%"):
            continue
        if ";" in line:
            return True
    if RANGE_RE.search(s):
        return True
    return False


def has_abcx_body(source: str) -> bool:
    """Strict: body actually contains a top-level `;` separator."""
    in_body = False
    for line in (source or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not in_body:
            if s.startswith("K:"):
                in_body = True
            continue
        if not s or s.startswith("%") or FIELD_RE.match(s):
            continue
        if len(split_top_level(strip_comment(s), ";")) > 1:
            return True
    return False


# ---------------------------------------------------------------------------
# Bar splitter for ABC music lines
# ---------------------------------------------------------------------------

@dataclass
class Measure:
    prefix: str
    content: str
    suffix: str


def _is_bar_start(text: str, i: int, quote: bool, bracket: int) -> bool:
    if quote or bracket > 0:
        return False
    ch = text[i]
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if ch == "|":
        return True
    if ch == ":" and nxt == "|":
        return True
    if ch == "[" and nxt == "|":
        return True
    return False


def _consume_bar(text: str, i: int) -> tuple:
    delim = ""
    if i < len(text) and text[i] in (":", "["):
        delim += text[i]
        i += 1
    if i < len(text) and text[i] == "|":
        delim += text[i]
        i += 1
    while i < len(text) and BAR_CHAR_RE.match(text[i]):
        delim += text[i]
        i += 1
    if i < len(text) and text[i].isdigit():
        delim += text[i]
        i += 1
    return delim, i


def split_abc_measures(text: str) -> list:
    result: list = []
    prefix = ""
    content = ""
    i = 0
    quote = False
    bracket = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            quote = not quote
            content += ch
            i += 1
            continue
        if not quote:
            if ch == "[" and (i + 1 >= len(text) or text[i + 1] != "|"):
                bracket += 1
                content += ch
                i += 1
                continue
            if ch == "]" and bracket > 0:
                bracket -= 1
                content += ch
                i += 1
                continue
        if _is_bar_start(text, i, quote, bracket):
            delim, i = _consume_bar(text, i)
            if not content.strip() and not prefix:
                prefix = delim
            else:
                result.append(Measure(prefix=prefix, content=content.strip(), suffix=delim))
                prefix = ""
                content = ""
        else:
            content += ch
            i += 1
    if content.strip():
        result.append(Measure(prefix=prefix, content=content.strip(), suffix=""))
    return result


# ---------------------------------------------------------------------------
# Duration rewriting
# ---------------------------------------------------------------------------

def format_multiplier(num: int, den: int) -> str:
    f = Fraction(num, den)
    n, d = f.numerator, f.denominator
    if d == 1:
        return "" if n == 1 else str(n)
    if n == 1 and d == 2:
        return "/"
    if n == 1:
        return f"/{d}"
    return f"{n}/{d}"


def rewrite_durations(content: str, factor_num: int, factor_den: int) -> str:
    """Multiply every note/rest/chord duration by factor_num/factor_den."""
    if factor_num == factor_den:
        return content
    out: list = []
    i = 0
    n = len(content)

    def consume_dur(start: int) -> tuple:
        m = DUR_RE.match(content, start)
        if not m or not m.group(0):
            return 1, 1, start
        num = int(m.group(1)) if m.group(1) else 1
        den = 1
        if m.group(2):
            den = int(m.group(3)) if m.group(3) else 2 ** len(m.group(2))
        return num, den, m.end()

    def skip_paired(start: int, close: str) -> int:
        idx = content.find(close, start + 1)
        return n if idx < 0 else idx + 1

    while i < n:
        ch = content[i]
        if ch == '"':
            e = skip_paired(i, '"')
            out.append(content[i:e])
            i = e
            continue
        if ch == "!":
            e = skip_paired(i, "!")
            out.append(content[i:e])
            i = e
            continue
        if ch == "{":
            e = skip_paired(i, "}")
            out.append(content[i:e])
            i = e
            continue
        if ch == "[" and i + 1 < n and INLINE_FIELD_RE.match(content[i + 1 : i + 3]):
            e = skip_paired(i, "]")
            out.append(content[i:e])
            i = e
            continue
        if ch == "[":
            e = skip_paired(i, "]")
            out.append(content[i:e])
            i = e
            num, den, end = consume_dur(i)
            out.append(format_multiplier(num * factor_num, den * factor_den))
            i = end
            continue
        m = NOTE_RE.match(content, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            num, den, end = consume_dur(i)
            out.append(format_multiplier(num * factor_num, den * factor_den))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# normalize_abc -- unify per-voice L: across an ABC score
# ---------------------------------------------------------------------------

def normalize_abc(source: str) -> str:
    """Detect per-voice L: declarations and rewrite all durations to a single
    unified L: (the finest one used). Returns source unchanged if all voices
    already share the same L:.
    """
    normalized = (source or "").replace("\r\n", "\n")
    lines = normalized.split("\n")

    global_l = Fraction(1, 8)
    header_lines: list = []
    body_lines: list = []
    in_body = False
    for line in lines:
        s = line.strip()
        if not in_body:
            m = L_RE.match(s)
            if m:
                global_l = Fraction(int(m.group(1)), int(m.group(2)))
            header_lines.append(line)
            if s.startswith("K:"):
                in_body = True
            continue
        body_lines.append(line)

    voice_ls: dict = {}
    info: list = []
    current_voice: Optional[str] = None
    current_l = global_l
    used_dens: set = {global_l.denominator}

    for line in body_lines:
        s = line.strip()
        m = V_FIELD_RE.match(s)
        if m:
            current_voice = m.group(1)
            current_l = voice_ls.get(current_voice, global_l)
            info.append({"line": line, "kind": "vfield"})
            continue
        m = L_RE.match(s)
        if m:
            current_l = Fraction(int(m.group(1)), int(m.group(2)))
            used_dens.add(current_l.denominator)
            if current_voice is not None:
                voice_ls[current_voice] = current_l
            else:
                global_l = current_l
            info.append({"line": line, "kind": "lfield"})
            continue
        if not s or s.startswith("%") or FIELD_RE.match(s):
            info.append({"line": line, "kind": "field"})
            continue
        info.append({"line": line, "kind": "music", "L": current_l})

    if len(used_dens) <= 1:
        return source

    unified_den = max(used_dens)
    unified_l = Fraction(1, unified_den)

    out_header: list = []
    has_l = False
    for line in header_lines:
        s = line.strip()
        if L_RE.match(s):
            out_header.append(f"L:{unified_l.numerator}/{unified_l.denominator}")
            has_l = True
            continue
        if s.startswith("K:") and not has_l:
            out_header.append(f"L:{unified_l.numerator}/{unified_l.denominator}")
            has_l = True
        out_header.append(line)

    out_body: list = []
    for it in info:
        if it["kind"] == "lfield":
            continue
        if it["kind"] == "music":
            old_l: Fraction = it["L"]
            fnum = unified_l.denominator * old_l.numerator
            fden = old_l.denominator * unified_l.numerator
            out_body.append(rewrite_durations(it["line"], fnum, fden))
        else:
            out_body.append(it["line"])

    return "\n".join(out_header) + "\n" + "\n".join(out_body) + "\n"


# ---------------------------------------------------------------------------
# ABC -> ABCX
# ---------------------------------------------------------------------------

def abc_to_abcx(source: str) -> str:
    """Multiplex per-voice ABC bars into ABCX `;`-separated measures."""
    if has_abcx_body(source):
        return source

    if not (source or "").strip():
        raise AbcError("Empty input.")

    normalized = (source or "").replace("\r\n", "\n")
    lines = normalized.split("\n")
    header_lines: list = []
    middle_lines: list = []
    raw_body: list = []
    phase = "header"
    saw_k = False

    for line in lines:
        s = line.strip()
        if phase == "header":
            header_lines.append(line)
            if s.startswith("K:"):
                phase = "middle"
                saw_k = True
            continue
        if phase == "middle":
            is_field = bool(FIELD_RE.match(s))
            is_directive = s.startswith("%")
            if not s or is_field or is_directive:
                middle_lines.append(line)
                continue
            phase = "body"
        raw_body.append(line)

    if not saw_k:
        raise AbcError("Missing K: line -- input is not a valid ABC file.")

    # Merge lines ending with backslash continuation
    merged: list = []
    buffer = ""
    for line in raw_body:
        if re.search(r"\\\s*$", line):
            buffer += re.sub(r"\\\s*$", " ", line)
        else:
            merged.append(buffer + line)
            buffer = ""
    if buffer:
        merged.append(buffer)

    voice_order: list = []
    voice_bars: dict = {}

    def ensure_voice(raw: str) -> str:
        norm = normalize_voice_name(raw)
        if norm not in voice_bars:
            voice_order.append(norm)
            voice_bars[norm] = []
        return norm

    for line in middle_lines:
        m = V_FIELD_RE.match(line.strip())
        if m:
            ensure_voice(m.group(1))

    current_voice = voice_order[0] if voice_order else ensure_voice("1")

    for line in merged:
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        m = V_FIELD_RE.match(s)
        if m:
            current_voice = ensure_voice(m.group(1))
            continue
        im = INLINE_V_RE.match(s)
        if im:
            voice_name = ensure_voice(im.group(1))
            content = im.group(2)
        else:
            voice_name = current_voice
            content = s
        cleaned = strip_comment(content).strip()
        if not cleaned:
            continue
        for measure in split_abc_measures(cleaned):
            voice_bars[voice_name].append(measure)

    out_header = list(header_lines)
    has_score = any(l.strip().startswith("%%score") for l in out_header)
    if not has_score:
        k_idx = -1
        for i in range(len(out_header) - 1, -1, -1):
            if out_header[i].strip().startswith("K:"):
                k_idx = i
                break
        score_line = "%%score " + " ".join(f"({v})" for v in voice_order)
        if k_idx >= 0:
            out_header.insert(k_idx, score_line)
        else:
            out_header.append(score_line)

    max_bars = max((len(voice_bars[v]) for v in voice_order), default=0)
    out_body: list = []
    sep = " ; " if len(voice_order) > 1 else ""

    for i in range(max_bars):
        parts = []
        for v in voice_order:
            bars = voice_bars[v]
            parts.append(bars[i].content.strip() if i < len(bars) else "z")
        prefix = ""
        suffix = ""
        for v in voice_order:
            bars = voice_bars[v]
            if i < len(bars):
                m = bars[i]
                if not prefix and m.prefix:
                    prefix = m.prefix
                if not suffix and m.suffix:
                    suffix = m.suffix
        row = ""
        if prefix:
            row += prefix + " "
        row += sep.join(parts)
        if suffix:
            row += " " + suffix
        out_body.append(row.strip())

    middle_str = ("\n".join(middle_lines) + "\n") if middle_lines else ""
    return "\n".join(out_header) + "\n" + middle_str + "\n".join(out_body) + "\n"


# ---------------------------------------------------------------------------
# ABCX -> ABC
# ---------------------------------------------------------------------------

_DECORATION_MAP = {
    "crescendo": ("!crescendo(!", "!crescendo)!"),
    "diminuendo": ("!diminuendo(!", "!diminuendo)!"),
    "pedal": ("!ped!", "!ped-up!"),
    "ottava8va": ("!8va(!", "!8va)!"),
    "ottava8vb": ("!8vb(!", "!8vb)!"),
    "trill": ("!trill(!", "!trill)!"),
    "slur": ("(", ")"),
    "phrase": ("(", ")"),
}


def _strip_explicit_ranges(source: str) -> str:
    def repl(m: re.Match) -> str:
        kind = m.group(3)
        paren = m.group(4)
        pair = _DECORATION_MAP.get(kind)
        if not pair:
            return ""
        return pair[0] if paren == "(" else pair[1]

    return RANGE_RE.sub(repl, source)


def _split_abcx_measures(line: str) -> list:
    """ABCX measure splitter (only `|` is a measure boundary; `;` is voice sep)."""
    result: list = []
    prefix = ""
    content = ""
    content_start = 0
    i = 0
    while i < len(line):
        if line[i] == "|":
            bar = line[i]
            i += 1
            while i < len(line) and BAR_CHAR_RE.match(line[i]):
                bar += line[i]
                i += 1
            if i < len(line) and line[i].isdigit():
                bar += line[i]
                i += 1
            if not content.strip() and not prefix:
                prefix = bar
                content_start = i
            else:
                result.append(
                    {"prefix": prefix, "content": content, "suffix": bar, "column": content_start}
                )
                prefix = ""
                content = ""
                content_start = i
        else:
            if not content:
                content_start = i
            content += line[i]
            i += 1
    if content.strip():
        result.append(
            {"prefix": prefix, "content": content, "suffix": "", "column": content_start}
        )
    return result


def _infer_voices_from_body(body_lines: list) -> list:
    count = 1
    for _, text in body_lines:
        parts = split_top_level(strip_comment(text), ";")
        if len(parts) > count:
            count = len(parts)
    return [f"V{i + 1}" for i in range(count)]


def _bar_suffix(content: str) -> str:
    s = content.strip()
    if re.search(r"[\]:|]$", s):
        return s
    return s + "|"


def abcx_to_abc(source: str, *, validate: bool = True) -> tuple:
    """Convert ABCX to ABC. Returns (abc_text, diagnostics).

    With validate=True, the first error diagnostic is raised as AbcError
    instead of returned. Warnings are always returned in the list.
    """
    if not (source or "").strip():
        raise AbcError("Empty input.")

    normalized = (source or "").replace("\r\n", "\n")
    text_lines = normalized.split("\n")
    prelude: list = []
    body_lines: list = []
    fields: dict = {}
    score_line: Optional[str] = None
    in_body = False
    saw_k = False

    for idx, text in enumerate(text_lines):
        trimmed = text.strip()
        is_prelude = (
            not trimmed
            or trimmed.startswith("%")
            or trimmed.startswith("%%")
            or bool(FIELD_RE.match(trimmed))
        )
        if not in_body and is_prelude:
            prelude.append(text)
            if trimmed.startswith("%%score"):
                score_line = text
            field_match = re.match(r"^([A-Za-z]):\s*(.*)$", trimmed)
            if field_match:
                fields[field_match.group(1)] = field_match.group(2)
                if field_match.group(1) == "K":
                    saw_k = True
        else:
            in_body = True
            body_lines.append((idx, text))

    if not saw_k:
        raise AbcError("Missing K: line -- input is not a valid ABC/ABCX file.")

    diagnostics: list = []
    if not score_line and is_abcx(source):
        diagnostics.append(Diagnostic("error", 0, 0, "ABCX requires a %%score voice/staff declaration."))

    voices = parse_score_voices(score_line) if score_line else _infer_voices_from_body(body_lines)

    open_ranges: dict = {}
    for line_no, text in body_lines:
        for m in RANGE_RE.finditer(text):
            key = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
            if m.group(4) == "(":
                if key in open_ranges:
                    diagnostics.append(Diagnostic("error", line_no, m.start(), f"Range marker {key} is already open."))
                open_ranges[key] = (line_no, m.start())
            else:
                if key not in open_ranges:
                    diagnostics.append(Diagnostic("error", line_no, m.start(), f"Range marker {key} closes before it opens."))
                else:
                    open_ranges.pop(key)
    for key, (line_no, col) in open_ranges.items():
        diagnostics.append(Diagnostic("error", line_no, col, f"Range marker {key} is not closed."))

    # convertBody-equivalent: produce a sequence of output lines
    converted: list = []
    for line_no, text in body_lines:
        if not text.strip():
            converted.append("")
            continue
        s = text.strip()
        if re.match(r"^\s*%", text) or FIELD_RE.match(s) or re.match(r"^\s*\[[A-Za-z]:", text):
            converted.append(text)
            continue

        measures = _split_abcx_measures(text)
        if not measures:
            converted.append(text)
            continue

        per_voice = ["" for _ in voices]
        for measure in measures:
            parts = split_top_level(measure["content"], ";")
            if len(parts) != len(voices):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        line_no,
                        measure["column"],
                        f"Expected {len(voices)} voice(s) from %%score, found {len(parts)}.",
                    )
                )
            for idx, _ in enumerate(voices):
                content = parts[idx] if idx < len(parts) else ""
                per_voice[idx] += (
                    f"{measure['prefix']}{_strip_explicit_ranges(content).strip()}{measure['suffix']}"
                )

        for idx, voice in enumerate(voices):
            converted.append(f"[V:{voice}] {per_voice[idx].strip()}")

    if validate:
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            d = errors[0]
            raise AbcError(d.message, d.line, d.column)

    # buildAbc-equivalent: partition into voice accumulators, pass-throughs to voice 0
    voice_accum: list = [[] for _ in voices]
    vline_re = re.compile(r"^\[V:([^\]]+)\]\s*(.*)$")
    for line in converted:
        if not line.strip():
            continue
        m = vline_re.match(line)
        if m:
            vname = normalize_voice_name(m.group(1))
            if vname in voices:
                voice_accum[voices.index(vname)].append(m.group(2).strip())
            else:
                voice_accum[0].append(line)
        else:
            voice_accum[0].append(line)

    # Header assembly
    header: list = []
    voice_definitions: set = set()
    key_line: Optional[str] = None
    has_score = False

    for line in prelude:
        s = line.strip()
        if not s:
            continue
        if s.startswith("%%score"):
            header.append(line)
            has_score = True
            continue
        if re.match(r"^K:", s):
            key_line = line
            continue
        v_match = re.match(r"^(\s*)V:\s*([^\s]+)(.*)$", line)
        if v_match:
            norm = normalize_voice_name(v_match.group(2))
            voice_definitions.add(norm)
            header.append(f"{v_match.group(1)}V:{strip_leading_v(norm)}{v_match.group(3)}")
            continue
        header.append(line)

    if not has_score and len(voices) > 1:
        header.append("%%score " + " ".join(f"({v})" for v in voices))

    for v in voices:
        if v not in voice_definitions:
            header.append(f"V:{strip_leading_v(v)}")

    if key_line:
        header.append(key_line)
    header.append("I:linebreak $")

    # Emit groups: for each index, all voices then $ on the last
    body_parts: list = []
    max_groups = max((len(a) for a in voice_accum), default=0)
    for g in range(max_groups):
        for v, voice in enumerate(voices):
            if g < len(voice_accum[v]):
                group_line = voice_accum[v][g]
                is_last = v == len(voices) - 1
                linebreak = "$" if is_last else ""
                body_parts.append(
                    f"[V:{strip_leading_v(voice)}] {_bar_suffix(group_line)}{linebreak}"
                )

    abc = "\n".join(header) + "\n" + "\n".join(body_parts).rstrip() + "\n"
    return abc, diagnostics


# ---------------------------------------------------------------------------
# Standard format facade
# ---------------------------------------------------------------------------

def _require_valid(source: str) -> None:
    if not (source or "").strip():
        raise AbcError("Empty input.")
    normalized = (source or "").replace("\r\n", "\n")
    if not re.search(r"^K:", normalized, re.MULTILINE):
        raise AbcError("Missing K: line -- input is not a valid ABC/ABCX file.")


def to_standard_abc(source: str, *, validate: bool = True) -> str:
    """Convert any ABC/ABCX input to standard ABC (unified L: across voices)."""
    _require_valid(source)
    if has_abcx_body(source):
        abc, _diagnostics = abcx_to_abc(source, validate=validate)
    else:
        abc = source
    return normalize_abc(abc)


def to_standard_abcx(source: str, *, validate: bool = True) -> str:
    """Convert any ABC/ABCX input to standard ABCX (unified L: across voices)."""
    _require_valid(source)
    if has_abcx_body(source):
        return normalize_abc(source)
    return abc_to_abcx(normalize_abc(source))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_input(path_or_dash: str) -> tuple:
    if path_or_dash == "-":
        return sys.stdin.read(), Path("stdin")
    p = Path(path_or_dash)
    if not p.exists():
        raise AbcError(f"Input file not found: {path_or_dash}")
    return p.read_text(encoding="utf-8"), p


def _default_output(input_path: Path, target: str) -> Path:
    return input_path.with_name(f"{input_path.stem}.std.{target}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert ABC/ABCX to standard format (unified L: across voices).",
    )
    parser.add_argument("input", help="Input file path (use - for stdin)")
    parser.add_argument(
        "-t", "--target",
        choices=("abcx", "abc"),
        default="abcx",
        help="Output format (default: abcx)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path. Defaults to <input>.std.<target>; use - for stdout",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write to stdout instead of a file",
    )
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Do not raise on ABCX structural errors",
    )
    args = parser.parse_args(argv)

    try:
        source, input_path = _read_input(args.input)
        if args.target == "abc":
            result = to_standard_abc(source, validate=args.validate)
        else:
            result = to_standard_abcx(source, validate=args.validate)
    except AbcError as e:
        print(f"to_standard: error: {e}", file=sys.stderr)
        return 2

    if args.stdout or args.output == "-":
        sys.stdout.write(result)
        return 0

    if args.output:
        out_path = Path(args.output)
    elif input_path.name == "stdin":
        sys.stdout.write(result)
        return 0
    else:
        out_path = _default_output(input_path, args.target)

    out_path.write_text(result, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
