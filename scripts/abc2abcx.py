#!/usr/bin/env python3
"""Convert standard ABC (xml2abc output) → ABCX.

Pipeline:
    .abc (per-voice V: blocks)
        |
        v  abc_to_abcx
    .abcx (; separated measures, row layout preserved)

CLI:
    python3 abc2abcx.py input.abc           # -> input.abcx
    python3 abc2abcx.py --batch dir/        # convert all .abc under dir

Import API:
    from abc2abcx import abc_to_abcx, to_standard_abcx, normalize_abc, AbcError
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
# Errors
# ---------------------------------------------------------------------------

class AbcError(Exception):
    """Raised on hard parse / structural failures."""

    def __init__(self, message: str, line: Optional[int] = None,
                 column: Optional[int] = None) -> None:
        loc = f" (line {line + 1}, col {column})" if line is not None else ""
        super().__init__(message + loc)
        self.line = line
        self.column = column
        self.raw_message = message


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

FIELD_RE = re.compile(r"^[A-Za-z]:")
L_RE = re.compile(r"^L:\s*(\d+)\s*/\s*(\d+)")
V_FIELD_RE = re.compile(r"^V:\s*(\S+)")
INLINE_V_RE = re.compile(r"^\[V:([^\]]+)\]\s*(.*)$")
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


def parse_score_voices(score_line: str) -> list:
    voices: list = []
    seen: set = set()
    text = score_line or ""
    text = re.sub(r"^\s*%%score\s+", "", text)
    brace_m = re.search(r"\{([^}]*)\}", text)
    if brace_m:
        inner = brace_m.group(1)
        for group in inner.split("|"):
            group = group.strip()
            if group.startswith("(") and group.endswith(")"):
                group = group[1:-1]
            for tok in group.split():
                norm = normalize_voice_name(tok)
                if norm and norm not in seen:
                    seen.add(norm)
                    voices.append(norm)
    for m in re.finditer(r"\(([^)]*)\)", text):
        for tok in m.group(1).strip().split():
            norm = normalize_voice_name(tok)
            if norm and norm not in seen:
                seen.add(norm)
                voices.append(norm)
    return voices


# ---------------------------------------------------------------------------
# Bar splitter
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
                result.append(Measure(prefix=prefix, content=content.strip(),
                                       suffix=delim))
                prefix = ""
                content = ""
        else:
            content += ch
            i += 1
    if content.strip():
        result.append(Measure(prefix=prefix, content=content.strip(), suffix=""))
    return result


# ---------------------------------------------------------------------------
# Duration rewriting (for L: normalization)
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
    INLINE_FIELD_RE = re.compile(r"^[A-Za-z]:")

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
            e = skip_paired(i, '!')
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
# normalize_abc -- preserve explicit M:/L: switches while normalizing line endings
# ---------------------------------------------------------------------------

def normalize_abc(source: str) -> str:
    """Return source with normalized line endings.

    ABCX allows synchronized M:/L: changes inside a tune, so we preserve them
    instead of collapsing the score to one global default length.
    """
    return (source or "").replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# ABC -> ABCX  (row-preserving!)
# ---------------------------------------------------------------------------

def abc_to_abcx(source: str) -> str:
    """Multiplex per-voice ABC bars into ABCX `;`-separated measures.

    KEY: preserves the source line structure — each source music line
    becomes one ABCX output line containing the same number of measures.

    Removes redundant piano-specific information:
    - %%MIDI directives (program, channel, control)
    - Explicit V: clef definitions (inferred from %%score)
    """
    if has_abcx_body(source):
        return source

    if not (source or "").strip():
        raise AbcError("Empty input.")

    normalized = (source or "").replace("\r\n", "\n")
    lines = normalized.split("\n")
    header_l = Fraction(1, 8)
    header_lines: list = []
    middle_lines: list = []
    raw_body: list = []
    phase = "header"
    saw_k = False

    for line in lines:
        s = line.strip()
        if phase == "header":
            # Skip redundant MIDI directives
            if s.startswith("%%MIDI"):
                continue
            header_lines.append(line)
            m = L_RE.match(s)
            if m:
                header_l = Fraction(int(m.group(1)), int(m.group(2)))
            if s.startswith("K:"):
                phase = "middle"
                saw_k = True
            continue
        if phase == "middle":
            is_field = bool(FIELD_RE.match(s))
            is_directive = s.startswith("%")
            if not s or is_field or is_directive:
                # Skip redundant MIDI directives and bare V: definitions
                if s.startswith("%%MIDI"):
                    continue
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
    # voice_rows[voice] = list of rows, each row = list of Measure
    voice_rows: dict = {}
    voice_l: dict = {}

    def ensure_voice(raw: str) -> str:
        norm = normalize_voice_name(raw)
        if norm not in voice_rows:
            voice_order.append(norm)
            voice_rows[norm] = []
        return norm

    for line in middle_lines:
        m = V_FIELD_RE.match(line.strip())
        if m:
            ensure_voice(m.group(1))

    middle_voice = voice_order[0] if voice_order else None
    for line in middle_lines:
        s = line.strip()
        m = V_FIELD_RE.match(s)
        if m:
            middle_voice = ensure_voice(m.group(1))
            continue
        m = L_RE.match(s)
        if m and middle_voice is not None:
            voice_l[middle_voice] = Fraction(int(m.group(1)), int(m.group(2)))

    # Filter out bare V: lines and V: lines with only clef/name from middle_lines
    # Keep only V: lines that have essential non-redundant information
    _V_WITH_CLEF_RE = re.compile(r"^V:\s*\S+\s+(treble|bass|alto|tenor|perc|tab|none)(\s|$)")
    _BARE_V_RE = re.compile(r"^V:\s*\S+\s*$")
    filtered_middle = []
    for line in middle_lines:
        s = line.strip()
        # Skip bare V: lines
        if _BARE_V_RE.match(s):
            continue
        # Skip V: lines with only clef and optional name (redundant for piano)
        if _V_WITH_CLEF_RE.match(s):
            # Check if it only has clef and name, no other attributes
            # V:1 treble nm="Piano" -> skip
            # V:1 treble transpose=2 -> keep
            if not any(attr in s for attr in ['transpose=', 'octave=', 'clef=', 'stafflines=', 'strings=', 'capo=']):
                continue
        filtered_middle.append(line)
    middle_lines = filtered_middle

    current_voice = voice_order[0] if voice_order else ensure_voice("1")
    current_l = header_l

    for line in merged:
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        m = V_FIELD_RE.match(s)
        if m:
            current_voice = ensure_voice(m.group(1))
            current_l = voice_l.get(current_voice, header_l)
            continue
        m = L_RE.match(s)
        if m:
            current_l = Fraction(int(m.group(1)), int(m.group(2)))
            voice_l[current_voice] = current_l
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
        active_l = voice_l.get(voice_name, header_l)
        if active_l != header_l:
            cleaned = rewrite_durations(
                cleaned,
                header_l.denominator * active_l.numerator,
                active_l.denominator * header_l.numerator,
            )
        # Each source music line → one row of measures.
        measures = split_abc_measures(cleaned)
        if measures:
            voice_rows[voice_name].append(measures)

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

    # Use the first voice's row count as the output row count.
    primary = voice_order[0] if voice_order else None
    max_rows = len(voice_rows.get(primary, []))

    out_body: list = []

    for row_idx in range(max_rows):
        # Gather per-voice measures for this row.
        row_measures: list = []  # row_measures[v_idx] = list[Measure]
        for v in voice_order:
            rows = voice_rows.get(v, [])
            row_measures.append(rows[row_idx] if row_idx < len(rows) else [])

        # Max measure count across voices in this row (should match primary).
        max_m = max((len(mv) for mv in row_measures), default=0)
        if max_m == 0:
            continue

        # Opening bar prefix from primary voice's first measure (e.g. "|:").
        opening = ""
        if row_measures and row_measures[0]:
            opening = row_measures[0][0].prefix

        out = opening
        for m_idx in range(max_m):
            voice_strs = []
            for v_idx in range(len(voice_order)):
                mv = row_measures[v_idx]
                if m_idx < len(mv):
                    voice_strs.append(mv[m_idx].content.strip())
                else:
                    voice_strs.append("z")
            if len(voice_order) > 1:
                group = " ; ".join(voice_strs)
            else:
                group = voice_strs[0]
            # Closing bar from primary voice's measure suffix.
            if m_idx < len(row_measures[0]):
                sfx = row_measures[0][m_idx].suffix or "|"
            else:
                sfx = "|"
            if out and not out.endswith(" "):
                out += " "
            out += group + " " + sfx
        out_body.append(out.strip())

    preserved_middle = []
    for line in middle_lines:
        if L_RE.match(line.strip()):
            continue
        preserved_middle.append(line)
    middle_str = ("\n".join(preserved_middle) + "\n") if preserved_middle else ""
    return "\n".join(out_header) + "\n" + middle_str + "\n".join(out_body) + "\n"


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

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


def to_standard_abcx(source: str, *, validate: bool = True) -> str:
    """Convert any ABC input to standard ABCX while preserving synchronized M:/L: changes."""
    if not (source or "").strip():
        raise AbcError("Empty input.")
    normalized = (source or "").replace("\r\n", "\n")
    if not re.search(r"^K:", normalized, re.MULTILINE):
        raise AbcError("Missing K: line -- input is not a valid ABC file.")
    if has_abcx_body(source):
        return normalize_abc(source)
    return abc_to_abcx(normalize_abc(source))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _convert_one(abc_path: Path, *, validate: bool = True) -> Path:
    abc_text = abc_path.read_text(encoding="utf-8")
    abcx_text = to_standard_abcx(abc_text, validate=validate)
    out_path = abc_path.with_suffix(".abcx")
    out_path.write_text(
        abcx_text if abcx_text.endswith("\n") else abcx_text + "\n",
        encoding="utf-8",
    )
    return out_path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert .abc (xml2abc output) to .abcx.",
    )
    parser.add_argument("input",
                        help=".abc file (single mode) OR root directory "
                             "(with --batch).")
    parser.add_argument("--batch", action="store_true",
                        help="Treat input as a directory; recurse and convert "
                             "all .abc files.")
    parser.add_argument("--no-validate", action="store_true",
                        help="Do not raise on ABCX structural errors.")
    args = parser.parse_args(argv)

    validate = not args.no_validate

    if args.batch:
        root = Path(args.input).expanduser().resolve()
        if not root.is_dir():
            parser.error(f"--batch input must be a directory: {root}")
        files = sorted(root.rglob("*.abc"))
        ok = 0
        failed = 0
        for i, abc_path in enumerate(files, 1):
            try:
                out_path = _convert_one(abc_path, validate=validate)
                ok += 1
                if i % 20 == 0 or i == len(files):
                    print(f"  [{i}/{len(files)}] ok={ok} failed={failed}",
                          file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(files)}] FAIL: {abc_path}: {e}",
                      file=sys.stderr)
        print(f"Done: {ok} ok, {failed} failed, {len(files)} total",
              file=sys.stderr)
        return 0

    abc_path = Path(args.input).expanduser().resolve()
    if not abc_path.exists():
        parser.error(f"Input file not found: {abc_path}")

    try:
        out_path = _convert_one(abc_path, validate=validate)
    except AbcError as e:
        print(f"abc2abcx: validation error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"abc2abcx: error: {e}", file=sys.stderr)
        return 2

    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
