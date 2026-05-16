# Aligned ABCX Format

## Overview

Aligned ABCX is a compact phrase/measure format derived from raw ABCX for SFT
data. Every aligned measure is represented as two output staves: upper staff
(`StaffU`) and lower staff (`StaffL`).

Raw scores do not have to be literally written as exactly two staves. During
conversion, `%%score` is projected to two target staves whenever that projection
is unambiguous. This keeps ordinary piano scores, two-voice shorthand scores,
and vocal+piano scores where the piano part is a braced two-staff group.

## Structure

```abcx
X:1
T:Title
C:Composer
L:1/16
Q:1/4=100
M:2/4
K:C
H1
M1	StaffU ; StaffL
M2	StaffU ; StaffL
H2
M3	StaffU ; StaffL
M4	StaffU ; StaffL
```

Required elements:

- `H1`, `H2`, ... mark phrases and appear on their own lines.
- `M1`, `M2`, ... mark measures and are followed by one TAB.
- Every `M` line contains exactly one semicolon: `StaffU ; StaffL`.
- `%%score` is removed because the staff split is encoded by the semicolon.
- Raw `V:` voice definitions are removed because aligned voice order is local to
  each measure.

## Score Projection Rules

Preferred raw layouts map directly:

```abcx
%%score { 1 | 2 }
%%score { (1 2 5) | (3 4 6) }
%%score 1 | 2
```

Relaxed layouts are projected:

```abcx
%%score 1 2
# => StaffU = voice 1, StaffL = voice 2

%%score 1 { (2 4) | (3 5) }
# => keep the braced piano group; drop the extra vocal/solo voice 1

%%score 1 | 2 | 3 | 4
# => fold top-level staves into two halves
```

Within each measure:

1. Split the raw measure by voice slots using `;`.
2. Map slots by the raw `%%score` voice order.
3. Join remaining voices within the same staff using ` & `.
4. Join the two staves using ` ; `.

Example:

```abcx
%%score { (1 2 5) | (3 4 6) }
raw:     A2B2 ; z4 ; z4 ; C,2D,2 ; E,4 ; z4
aligned: A2B2 ; C,2D,2 & E,4
```

## Rest Trimming

For each staff, inspect voices from back to front. If a trailing voice is all
rests, remove that voice and its preceding `&`.

If all voices in a staff are removed, use `.` as the staff placeholder.

```abcx
M7	c2d2 & e2f2 ; C,4
M8	. ; G,,4
M9	a4 ; .
```

Rest-only voices include empty slots, `.`, and voices containing only rests such
as `z12`, even when they include inline fields like `[K:bass]` or annotations.

## Conversion Pipeline

Both raw orphan ABCX conversion and score-MIDI aligned generation use the same
normalization rule:

1. Read raw ABCX.
2. Project `%%score` into two target staves.
3. Parse measure content.
4. Collapse voices to `StaffU ; StaffL`.
5. Remove trailing all-rest voices per staff.
6. Remove `%%score` and `V:` header lines.
7. Write phrase (`H`) and measure (`M`) aligned ABCX.

Current entry points:

- Orphan scores: `process_orphan_abcx.py`
- Paired score/performance pipeline: `scripts/align_score_performance.py`
- Score-only regeneration helper: `scripts/regenerate_score_files.py`

## Validation

A valid aligned ABCX file must satisfy:

- No `%%score` lines.
- No raw `V:` voice-definition lines.
- Every `M` line has a TAB after the measure id.
- Every `M` line has exactly one semicolon.
- The semicolon separates `StaffU` and `StaffL`.
- Empty staves are represented by `.`.
