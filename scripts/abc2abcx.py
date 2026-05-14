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


def _fix_score_directive(score_line: str, voice_order: list) -> str:
    """Fix %%score directive format for grand staff notation.

    xml2abc outputs patterns like:
    - `{ 1 | 2 }` for 2-voice scores → keep as-is (no parentheses needed for single voices)
    - `{ ( 1 3 ) | 2 }` for mixed format → needs to become `{ (1 3) | 2 }` (remove extra spaces)
    - `(1) (2)` for 2-voice without braces → needs to become `{ 1 | 2 }` (add braces and pipe)

    Examples:
        { 1 | 2 } → { 1 | 2 }  (unchanged - single voices don't need parentheses)
        { ( 1 4 ) | ( 2 3 ) } → { (1 4) | (2 3) }  (FIXED - remove extra spaces)
        { ( 1 3 ) | 2 } → { (1 3) | 2 }  (FIXED - remove extra spaces)
        { (1 3 5) | (2 4) } → { (1 3 5) | (2 4) }  (unchanged)
        (1) (2) → { 1 | 2 }  (FIXED - add braces and pipe for 2-voice)
    """
    # Remove %%score prefix
    content = re.sub(r"^\s*%%score\s+", "", score_line.strip())

    # Check if it has braces (grand staff format)
    brace_match = re.match(r"^\{\s*(.+?)\s*\}$", content)

    if brace_match:
        # Grand staff format: { ... | ... }
        inner = brace_match.group(1)
        groups = inner.split("|")
        fixed_groups = []

        for group in groups:
            group = group.strip()

            # Check if group has parentheses with extra spaces inside
            # Pattern: ( digit digit ... ) with spaces
            if re.match(r"^\(\s+[0-9\s]+\s+\)$", group):
                # Has parentheses with extra spaces - normalize
                voices = re.findall(r"\d+", group)
                if len(voices) == 1:
                    # Single voice - remove parentheses
                    fixed_groups.append(voices[0])
                else:
                    # Multiple voices - keep parentheses, remove extra spaces
                    fixed_groups.append(f"({' '.join(voices)})")
            elif re.match(r"^\([0-9\s]+\)$", group):
                # Has parentheses - check if single or multiple voices
                voices = re.findall(r"\d+", group)
                if len(voices) == 1:
                    # Single voice - remove parentheses
                    fixed_groups.append(voices[0])
                else:
                    # Multiple voices - keep parentheses
                    fixed_groups.append(f"({' '.join(voices)})")
            elif re.match(r"^[0-9\s]+$", group):
                # Bare voice numbers - keep as-is if single, add parentheses if multiple
                voices = group.split()
                if len(voices) == 1:
                    fixed_groups.append(voices[0])
                else:
                    fixed_groups.append(f"({' '.join(voices)})")
            else:
                # Unknown format, keep as-is
                fixed_groups.append(group)

        return f"%%score {{ {' | '.join(fixed_groups)} }}"

    # Check if it's a simple 2-voice format without braces: (1) (2)
    # This should be converted to grand staff format: { 1 | 2 }
    simple_match = re.match(r"^\((\d+)\)\s+\((\d+)\)$", content)
    if simple_match:
        voice1 = simple_match.group(1)
        voice2 = simple_match.group(2)
        return f"%%score {{ {voice1} | {voice2} }}"

    # All other cases: return unchanged
    return score_line.strip()


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
    if ch == ":" and nxt == ":":
        return True
    # Handle `: ` (repeat-end colon followed by space) — happens after
    # _strip_dollar removes `$` from patterns like `:|2$ A,2E2`.
    if ch == ":" and nxt == " ":
        # Look past whitespace for a bar character or digit
        j = i + 1
        while j < len(text) and text[j] == " ":
            j += 1
        if j < len(text) and text[j] in ("|", ":", "[") or (j < len(text) and text[j].isdigit()):
            return True
    return False


def _consume_bar(text: str, i: int) -> tuple:
    delim = ""
    if i < len(text) and text[i] in (":", "["):
        delim += text[i]
        i += 1
    if i < len(text) and text[i] in ("|", ":"):
        delim += text[i]
        i += 1
    while i < len(text) and text[i] in ("|", ":"):
        delim += text[i]
        i += 1
    # Consume complete volta number(s) after bar line
    # e.g., `2`, `1,3`, `1,2,3`, `"1,2"` (quoted text endings)
    if i < len(text) and text[i].isdigit():
        start = i
        while i < len(text) and (text[i].isdigit() or text[i] == ','):
            delim += text[i]
            i += 1
        # If followed by a space and more digits (volta text), consume that too
        if i < len(text) and text[i] == ' ' and i + 1 < len(text) and text[i + 1].isdigit():
            pass  # stop before the space, leave it as separator
    # Also consume trailing bar chars like `]` after volta numbers (e.g. `:|2]`)
    while i < len(text) and text[i] in ("|", ":", "]"):
        delim += text[i]
        i += 1
    # Handle whitespace between bar characters, e.g. `: | 2` after _strip_dollar.
    # Bar line characters in ABC must be contiguous (`:|`, `|:`, `||`, `|]`);
    # whitespace between them is consumed and NOT included in the delimiter.
    # Only consume `|` and `:`, NOT `[` (which starts chords/inline fields).
    # Also consume volta numbers that appear after whitespace.
    if i < len(text) and text[i] == " ":
        j = i
        while j < len(text) and text[j] == " ":
            j += 1
        if j < len(text) and (text[j] in ("|", ":", "]") or text[j].isdigit()):
            # Consume bar chars found after whitespace — NO space in delimiter.
            if text[j] in ("|", ":", "]"):
                delim += text[j]
                j += 1
                while j < len(text) and text[j] in ("|", ":"):
                    delim += text[j]
                    j += 1
                # After bar chars, skip more whitespace and check for volta number
                while j < len(text) and text[j] == " ":
                    j += 1
            # Consume volta number (may have appeared directly after whitespace,
            # or after whitespace+bar chars)
            if j < len(text) and text[j].isdigit():
                while j < len(text) and (text[j].isdigit() or text[j] == ','):
                    delim += text[j]
                    j += 1
            # Also consume trailing bar chars like `]` after volta numbers
            while j < len(text) and text[j] in ("|", ":", "]"):
                delim += text[j]
                j += 1
            i = j
    return delim, i


def split_abc_measures(text: str) -> list:
    result: list = []
    prefix = ""
    content = ""
    i = 0
    quote = False
    bracket = 0
    def _append(prefix: str, content: str, suffix: str) -> None:
        """Append a measure, filtering out non-musical segments."""
        stripped = content.strip()
        if not stripped:
            return
        # Skip linebreak-only segments.
        if stripped == '$':
            return
        # Skip ABC field directives (L:, M:, K:, etc.) that leaked into music.
        if FIELD_RE.match(stripped):
            return
        # Skip comment-only segments.
        if stripped.startswith('%'):
            return
        result.append(Measure(prefix=prefix, content=stripped, suffix=suffix))

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
                # Consecutive bars — absorb into prefix, don't create empty measure.
                prefix = delim
            elif not content.strip() and prefix:
                # Consecutive bars with existing prefix — merge into prefix.
                prefix = (prefix + ' ' + delim).strip()
            else:
                _append(prefix, content, delim)
                prefix = ""
                content = ""
        else:
            content += ch
            i += 1
    _append(prefix, content, "")
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
        # Skip lyric lines — xml2abc emits these for fingering annotations,
        # and they must not be parsed as music measures.
        if s.startswith(("w:", "W:", "s:")):
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

    # FIX 1: Fix %%score directive format
    # xml2abc generates %%score with correct structure but missing parentheses
    # Example: { 1 | 2 } should be { (1) | (2) }
    # Example: { (1 3 5) | (2 4) } is already correct
    # We need to:
    # 1. Find existing %%score line
    # 2. Parse and preserve braces { } and pipe | structure
    # 3. Ensure each voice group has parentheses
    # 4. Normalize voice names (1 → V1)

    existing_score_idx = -1
    existing_score_line = None
    for i, line in enumerate(out_header):
        if line.strip().startswith("%%score"):
            existing_score_idx = i
            existing_score_line = line.strip()
            break

    if existing_score_line:
        # Parse the existing %%score line and fix it
        score_line = _fix_score_directive(existing_score_line, voice_order)
        out_header[existing_score_idx] = score_line
    else:
        # No %%score line exists, generate one
        # For 2-voice scores, use grand staff format
        if len(voice_order) == 2:
            score_line = f"%%score {{ ({voice_order[0]}) | ({voice_order[1]}) }}"
        else:
            score_line = "%%score " + " ".join(f"({v})" for v in voice_order)

        # Insert before K:
        k_idx = -1
        for i in range(len(out_header) - 1, -1, -1):
            if out_header[i].strip().startswith("K:"):
                k_idx = i
                break
        if k_idx >= 0:
            out_header.insert(k_idx, score_line)
        else:
            out_header.append(score_line)

    # Flatten each voice's rows into a single measure list and align by index.
    # xml2abc can produce different numbers of music lines per voice (line-breaks
    # are inserted independently), but the measure order is the same — measure N
    # in V:1 corresponds to measure N in V:2.  We simply concatenate all measures
    # from each voice and align 1:1 by index.
    voice_measures: dict[str, list] = {}
    for v in voice_order:
        flat: list = []
        for row in voice_rows.get(v, []):
            flat.extend(row)
        # Remove $-only measures (linebreak markers that became standalone measures)
        filtered: list = []
        for m in flat:
            stripped = m.content.strip()
            if stripped == '$':
                # Linebreak-only — merge into previous measure's suffix
                if filtered:
                    prev = filtered[-1]
                    prev.suffix = (prev.suffix + ' $').strip()
                continue
            filtered.append(m)
        voice_measures[v] = filtered

        # Merge orphaned first-ending markers (content is just a digit like "1"
        # optionally followed by a comment `%N`) and stray repeat-end `:`
        # markers (produced when `:|` is split by bar parsing) into the
        # previous measure's suffix. xml2abc puts the first ending's music on
        # the line BEFORE the `|1` marker in some voices, and splits `:|` into
        # separate bar and `:` segments.
        merged: list = []
        for m in filtered:
            c = m.content.strip()
            # Match: orphaned first-ending digit, or stray `:` from repeat split.
            is_orphan = (
                re.match(r"^\d+(\s*%\s*\d+)?\s*$", c)
                or c == ':'
            )
            if is_orphan and not m.suffix and merged:
                prev = merged[-1]
                if m.prefix:
                    prev.suffix = (prev.suffix + ' ' + m.prefix).strip()
            else:
                merged.append(m)
        voice_measures[v] = merged

    # FIX 2: Extract tempo/expression marks from first measure and move to header
    # Tempo marks like "^Allegro", "^Andante", etc. should be in the header as %%text
    # directives, not in the first measure, since they apply to the entire piece.
    tempo_marks = []
    if voice_order and voice_order[0] in voice_measures:
        first_voice_measures = voice_measures[voice_order[0]]
        if first_voice_measures:
            first_measure = first_voice_measures[0]
            content = first_measure.content
            # Extract tempo marks: "^text" or "^text with spaces" at the start
            # Match patterns like "^Allegro", "^Lento, ma non troppo", etc.
            tempo_pattern = re.compile(r'^("(?:\^|_)[^"]+"\s*)+')
            match = tempo_pattern.match(content)
            if match:
                tempo_text = match.group(0)
                # Extract the actual text from quotes
                for m in re.finditer(r'"(\^|_)([^"]+)"', tempo_text):
                    mark_text = m.group(2).strip()
                    if mark_text:
                        tempo_marks.append(mark_text)
                # Remove tempo marks from first measure content
                first_measure.content = content[match.end():].lstrip()

    max_m = max((len(ms) for ms in voice_measures.values()), default=0)
    if max_m == 0:
        raise AbcError("No measures found in any voice.")

    # Group output measures into rows with a character limit for readability.
    out_body: list = []
    chars_per_row = 100  # target max line length
    current_line = ""

    for m_idx in range(max_m):
        voice_strs: list = []
        bar_sfx = "|"
        for v_idx, v in enumerate(voice_order):
            mv = voice_measures.get(v, [])
            if m_idx < len(mv):
                voice_strs.append(mv[m_idx].content.strip())
                if v_idx == 0:
                    bar_sfx = mv[m_idx].suffix or "|"
            else:
                voice_strs.append("z")

        group = " ; ".join(voice_strs)
        # Use the first voice's measure prefix for opening bar (e.g. "|:").
        first_mv = voice_measures.get(voice_order[0], [])
        opening = first_mv[m_idx].prefix if m_idx < len(first_mv) else ""

        # FIX: If group starts with a bare volta number and no opening, add a barline
        # Match patterns like `2 A2e2`, `2A,2E2`, `1,3 A2e2`, `2S C2`, `1[`, `2[`, `1!pp!`, etc.
        if not opening and re.match(r'^\d+(?:,\d+)*[\s\[\!A-Ga-gSZ]', group):
            opening = '|'

        # FIX 3: Add space between bar line and inline field to avoid parse ambiguity
        # When opening ends with bar characters (|, :) and group starts with [,
        # ensure there's a space between them to prevent ABCJS from misinterpreting
        # patterns like "|[K:B]" or "| |[K:B]" as first-ending brackets "|[1"
        if opening and group.startswith('['):
            # If opening doesn't already end with whitespace, add a space
            if not opening[-1].isspace():
                opening = opening + ' '

        segment = f"{opening}{group} {bar_sfx}" if opening else f"{group} {bar_sfx}"

        if current_line and len(current_line) + len(segment) + 3 > chars_per_row:
            out_body.append(current_line.rstrip())
            current_line = segment + " "
        else:
            current_line += segment + " "

    if current_line.strip():
        out_body.append(current_line.rstrip())

    preserved_middle = []
    for line in middle_lines:
        if L_RE.match(line.strip()):
            continue
        preserved_middle.append(line)

    # Add extracted tempo marks to middle section as %%text directives
    if tempo_marks:
        for mark in tempo_marks:
            preserved_middle.append(f"%%text {mark}")

    middle_str = ("\n".join(preserved_middle) + "\n") if preserved_middle else ""
    return "\n".join(out_header) + "\n" + middle_str + "\n".join(out_body) + "\n"


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

def to_standard_abcx(source: str, *, validate: bool = True) -> str:
    """Convert ABC input to standard ABCX format.

    Always applies formatting fixes:
    1. Grand staff format for 2-staff piano scores
    2. Tempo marks moved to header
    3. Space between bar lines and inline fields
    """
    if not (source or "").strip():
        raise AbcError("Empty input.")
    normalized = (source or "").replace("\r\n", "\n")
    if not re.search(r"^K:", normalized, re.MULTILINE):
        raise AbcError("Missing K: line -- input is not a valid ABC file.")
    # Always convert through abc_to_abcx to apply all fixes
    return abc_to_abcx(normalized)


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
