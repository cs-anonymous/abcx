#!/usr/bin/env python3
"""Convert ABCX → standard ABC v2.1.

CLI:
    python3 abcx2abc.py input.abcx           # -> input.abc
    python3 abcx2abc.py input.abcx -o out.abc
    python3 abcx2abc.py --batch dir/         # convert all .abcx under dir

Import API:
    from abcx2abc import abcx_to_abc, AbcError
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Errors / diagnostics
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
V_FIELD_RE = re.compile(r"^V:\s*(\S+)")
RANGE_RE = re.compile(
    r"@\[([A-Za-z0-9_.]+):([A-Za-z0-9_-]+):([A-Za-z0-9_-]+)([()])"
)
BAR_CHAR_RE = re.compile(r"[:|\]\[]")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

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
    text = score_line or ""
    text = re.sub(r"^\s*%%score\s+", "", text)
    # ABC %%score permits a mix of grouping syntax, e.g.
    # `{ ( 1 2 3 ) ( 4 5 ) }`, `{ ( 1 4 ) | ( 2 3 ) }`, or `( 1 2 ) 3`.
    # For ABCX voice demultiplexing we only need the ordered unique voice ids;
    # braces, parentheses, and staff bars are layout hints.
    for tok in re.findall(r"\bv?\d+\b", text, flags=re.IGNORECASE):
        norm = normalize_voice_name(tok)
        if norm and norm not in seen:
            seen.add(norm)
            voices.append(norm)
    return voices


def infer_clef_from_score(score_line: str, voice: str) -> str:
    """Infer clef for a voice from %%score layout.

    For piano scores with pattern { (V1 V3) | (V2 V4) }:
    - Left group (before |) = treble (right hand)
    - Right group (after |) = bass (left hand)
    """
    if not score_line:
        return "treble"

    text = score_line.strip()
    text = re.sub(r"^\s*%%score\s+", "", text)

    # Extract brace content: { (V1 V3) | (V2 V4) }
    brace_m = re.search(r"\{([^}]*)\}", text)
    if brace_m:
        inner = brace_m.group(1)
        groups = inner.split("|")

        # First group (left of |) = treble
        if len(groups) >= 1:
            left_voices = []
            for tok in groups[0].replace("(", "").replace(")", "").split():
                left_voices.append(normalize_voice_name(tok))
            if voice in left_voices:
                return "treble"

        # Second group (right of |) = bass
        if len(groups) >= 2:
            right_voices = []
            for tok in groups[1].replace("(", "").replace(")", "").split():
                right_voices.append(normalize_voice_name(tok))
            if voice in right_voices:
                return "bass"

    # Default to treble
    return "treble"


def is_abcx(source: str) -> bool:
    s = source or ""
    if re.search(r"^\s*%%score\s+", s, re.MULTILINE):
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


# ---------------------------------------------------------------------------
# Bar splitter for ABC music lines
# ---------------------------------------------------------------------------

def split_abc_measures(text: str) -> list:
    """Split ABC music text into measures. Returns list of dicts
    with prefix/content/suffix keys."""
    result: list = []
    prefix = ""
    content = ""
    content_start = 0
    i = 0
    quote = False
    bracket = 0

    def _is_bar_start() -> bool:
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

    def _consume_bar() -> tuple:
        delim = ""
        nonlocal i
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
        if _is_bar_start():
            delim, i = _consume_bar()
            if not content.strip() and not prefix:
                prefix = delim
            else:
                result.append({"prefix": prefix, "content": content,
                               "suffix": delim, "column": content_start})
                prefix = ""
                content = ""
                content_start = i
        else:
            content += ch
            i += 1
    if content.strip():
        result.append({"prefix": prefix, "content": content,
                       "suffix": "", "column": content_start})
    return result


# ---------------------------------------------------------------------------
# Explicit range stripping
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


def _bar_suffix(content: str) -> str:
    s = content.strip()
    if re.search(r"[\]:|]$", s):
        return s
    return s + "|"


# ---------------------------------------------------------------------------
# ABCX -> ABC
# ---------------------------------------------------------------------------

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
        diagnostics.append(Diagnostic("error", 0, 0,
            "ABCX requires a %%score voice/staff declaration."))

    voices = parse_score_voices(score_line) if score_line else []
    if not voices:
        # Infer from body.
        count = 1
        for _, text in body_lines:
            parts = split_top_level(strip_comment(text), ";")
            if len(parts) > count:
                count = len(parts)
        voices = [f"V{i + 1}" for i in range(count)]

    # Validate range markers.
    open_ranges: dict = {}
    for line_no, text in body_lines:
        for m in RANGE_RE.finditer(text):
            key = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
            if m.group(4) == "(":
                if key in open_ranges:
                    diagnostics.append(Diagnostic("error", line_no, m.start(),
                        f"Range marker {key} is already open."))
                open_ranges[key] = (line_no, m.start())
            else:
                if key not in open_ranges:
                    diagnostics.append(Diagnostic("error", line_no, m.start(),
                        f"Range marker {key} closes before it opens."))
                else:
                    open_ranges.pop(key)
    for key, (line_no, col) in open_ranges.items():
        diagnostics.append(Diagnostic("error", line_no, col,
            f"Range marker {key} is not closed."))

    # Convert: each ABCX visual line → one ABC line per voice.
    # Per ABCX v0.1 §3.1: measure is the outer group, voices within the
    # measure are separated by ";". A visual line may contain multiple
    # measures, separated by "|": "V1m1 ; V2m1 | V1m2 ; V2m2 |"
    converted: list = []
    for line_no, text in body_lines:
        if not text.strip():
            converted.append("")
            continue
        s = text.strip()
        if re.match(r"^\s*%", text) or FIELD_RE.match(s):
            converted.append(text)
            continue

        # Split by | into per-measure chunks (each carries prefix/content/suffix).
        measures = split_abc_measures(s)
        if not measures:
            converted.append("")
            continue

        per_voice = ["" for _ in voices]

        for m in measures:
            # Split the measure content by ; to get each voice's content
            # for this single measure.
            voice_contents = split_top_level(m["content"], ";")
            if len(voice_contents) != len(voices):
                diagnostics.append(Diagnostic("error", line_no, m["column"],
                    f"Expected {len(voices)} voice(s) from %%score, "
                    f"found {len(voice_contents)} in this measure."))
            for v_idx in range(len(voices)):
                vc = voice_contents[v_idx] if v_idx < len(voice_contents) else ""
                per_voice[v_idx] += (
                    f"{m['prefix']}"
                    f"{_strip_explicit_ranges(vc.strip()) or 'z'}"
                    f"{m['suffix']}"
                )

        converted.append("\t".join(pv.strip() for pv in per_voice))

    if validate:
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            d = errors[0]
            raise AbcError(d.message, d.line, d.column)

    # Partition converted lines into per-voice accumulators.
    voice_accum: list = [[] for _ in voices]

    for line in converted:
        if not line.strip():
            continue
        s = line.strip()
        if FIELD_RE.match(s) or re.match(r"^\s*%", line):
            # Field lines — skip for voice blocks (they stay in header).
            continue
        parts = line.split("\t")
        if len(parts) == len(voices):
            for idx, content in enumerate(parts):
                voice_accum[idx].append(content.strip())
        elif len(parts) == 1:
            voice_accum[0].append(parts[0].strip())
        else:
            voice_accum[0].append(line)

    # Header assembly.
    header: list = []
    voice_definitions: dict = {}
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
            voice_definitions[norm] = (
                f"{v_match.group(1)}V:{strip_leading_v(norm)}{v_match.group(3)}"
            )
            continue
        # Per-voice directives (%%MIDI, bare L:) go into voice blocks.
        if s.startswith("%%MIDI") or (
            re.match(r"^L:\s*(\d+)\s*/\s*(\d+)", s) and len(voices) > 1
        ):
            last_v = list(voice_definitions.keys())[-1] if voice_definitions else None
            if last_v:
                voice_definitions.setdefault(
                    last_v, f"V:{strip_leading_v(last_v)}"
                )
                voice_definitions[last_v] += "\n" + line
            continue
        header.append(line)

    if not has_score and len(voices) > 1:
        header.append("%%score " + " ".join(f"({v})" for v in voices))

    if key_line:
        header.append(key_line)
    header.append("I:linebreak $")

    # Emit voice blocks with auto-generated clef and MIDI settings.
    body_parts: list = []
    max_lines = max((len(a) for a in voice_accum), default=0)

    for v, voice in enumerate(voices):
        # Get or generate voice definition
        if voice in voice_definitions:
            decl = voice_definitions[voice]
        else:
            # Auto-generate voice definition with inferred clef
            clef = infer_clef_from_score(score_line, voice)
            voice_num = strip_leading_v(voice)
            decl = f"V:{voice_num} {clef}"
            # Add name for first voice
            if v == 0:
                decl += ' nm="Piano"'

        body_parts.append(decl)

        # Add standard piano MIDI settings
        body_parts.append("%%MIDI program 0")
        body_parts.append("%%MIDI control 7 100")
        body_parts.append("%%MIDI control 10 64")

        # Add music lines
        for i in range(max_lines):
            if i < len(voice_accum[v]):
                line_content = voice_accum[v][i]
                body_parts.append(_bar_suffix(line_content))
            else:
                body_parts.append("z |")

    abc = "\n".join(header) + "\n" + "\n".join(body_parts).rstrip() + "\n"
    return abc, diagnostics


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _convert_one(abcx_path: Path, *, validate: bool = True) -> Path:
    abcx_text = abcx_path.read_text(encoding="utf-8")
    abc_text, _diagnostics = abcx_to_abc(abcx_text, validate=validate)
    out_path = abcx_path.with_suffix(".abc")
    out_path.write_text(
        abc_text if abc_text.endswith("\n") else abc_text + "\n",
        encoding="utf-8",
    )
    return out_path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert .abcx to standard .abc v2.1.",
    )
    parser.add_argument("input",
                        help=".abcx file (single mode) OR root directory "
                             "(with --batch).")
    parser.add_argument("-o", "--output",
                        help="Output file path (single mode only).")
    parser.add_argument("--batch", action="store_true",
                        help="Treat input as a directory; recurse and convert "
                             "all .abcx files.")
    parser.add_argument("--no-validate", action="store_true",
                        help="Do not raise on ABCX structural errors.")
    args = parser.parse_args(argv)

    validate = not args.no_validate

    if args.batch:
        root = Path(args.input).expanduser().resolve()
        if not root.is_dir():
            parser.error(f"--batch input must be a directory: {root}")
        files = sorted(root.rglob("*.abcx"))
        ok = 0
        failed = 0
        for i, abcx_path in enumerate(files, 1):
            try:
                out_path = _convert_one(abcx_path, validate=validate)
                ok += 1
                if i % 20 == 0 or i == len(files):
                    print(f"  [{i}/{len(files)}] ok={ok} failed={failed}",
                          file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(files)}] FAIL: {abcx_path}: {e}",
                      file=sys.stderr)
        print(f"Done: {ok} ok, {failed} failed, {len(files)} total",
              file=sys.stderr)
        return 0

    abcx_path = Path(args.input).expanduser().resolve()
    if not abcx_path.exists():
        parser.error(f"Input file not found: {abcx_path}")

    try:
        if args.output:
            out_path = Path(args.output)
            abcx_text = abcx_path.read_text(encoding="utf-8")
            abc_text, _ = abcx_to_abc(abcx_text, validate=validate)
            out_path.write_text(
                abc_text if abc_text.endswith("\n") else abc_text + "\n",
                encoding="utf-8",
            )
        else:
            out_path = _convert_one(abcx_path, validate=validate)
    except AbcError as e:
        print(f"abcx2abc: validation error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"abcx2abc: error: {e}", file=sys.stderr)
        return 2

    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
