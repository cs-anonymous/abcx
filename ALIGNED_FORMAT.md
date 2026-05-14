# Aligned ABCX Format

## Overview

Aligned ABCX is a phrase-aligned music notation format for piano scores, designed to represent the alignment between left and right hand parts at the phrase and measure level.

## Format Structure

### File Extension
Use `.abcx` extension (same as standard ABCX).

### Basic Structure

```abcx
X:1
T:Title
C:Composer
%%score { 1 | 2 }
L:1/16
Q:1/4=100
M:2/4
K:C
H1
M1	right_hand_notes ; left_hand_notes
M2	right_hand_notes ; left_hand_notes
H2
M3	right_hand_notes ; left_hand_notes
M4	right_hand_notes ; left_hand_notes
```

### Key Elements

- **H markers** (phrases): `H1`, `H2`, `H3`, ... on separate lines
- **M markers** (measures): `M1`, `M2`, `M3`, ... followed by TAB character
- **Voice separator**: Use semicolon `;` to separate right and left hand
- **TAB character**: Required between M marker and note content (not spaces)

## Features

### Syntax Highlighting
- **H markers**: Cyan/teal (#4EC9B0), bold
- **M markers**: Yellow (#DCDCAA), bold
- Numbers follow their marker's color

### Preview
- Automatically converts to standard ABC format
- Each phrase renders on a single line
- Measures separated by bar lines
- Supports multi-phrase rendering

### Export
- MIDI export
- SVG export
- Standard ABC export
- Standard ABCX export

## Quick Start

1. **Create a file** with `.abcx` extension
2. **Write content** using H/M markers with TAB separation
3. **Open in VS Code** - syntax highlighting activates automatically
4. **Click preview button** to render the score
5. **Export** using buttons in preview panel

## Example

```abcx
X:1
T:Simple Example
C:Anonymous
%%score { 1 | 2 }
L:1/4
M:4/4
K:C
H1
M1	C D E F ; C, D, E, F,
M2	G A B c ; G, A, B, C
H2
M3	c B A G ; C B, A, G,
M4	F E D C ; F, E, D, C,
```

## Installation

```bash
# Package the extension
cd abcx
npx vsce package

# Install in VS Code
code --install-extension abcx-tools-0.3.3.vsix --force

# Reload VS Code
# Press Ctrl+Shift+P → "Developer: Reload Window"
```

## Testing

Run the test suite:
```bash
cd abcx
node test/test_aligned_format.js
```

## Troubleshooting

### Syntax highlighting not working
1. Ensure file extension is `.abcx`
2. Reload VS Code window
3. Check format: H markers on separate lines, M markers with TAB

### Preview shows only first phrase
This issue is fixed in v0.3.3. Update to the latest version.

### Preview shows errors
1. Check diagnostics at top of preview panel
2. Ensure each measure has `;` separator
3. Verify TAB characters (not spaces) after M markers
4. Check that all measures have closing bar lines

## Technical Details

### Conversion Process

Aligned ABCX is converted to standard ABC for rendering:

1. Collect all V:1 (right hand) content from all phrases
2. Collect all V:2 (left hand) content from all phrases
3. Output as standard ABC:
   ```abc
   V:1
   [all right hand content, one phrase per line]
   V:2
   [all left hand content, one phrase per line]
   ```

### Bar Line Handling

- Measures automatically separated by `|`
- Closing bar line added at end of each phrase
- Regex ensures chord brackets `]` are not mistaken for bar lines

## Documentation

- [ABC Notation Specification](ABC%20乐谱格式规范.md)
- [ABCX Extension Specification](ABCX%20扩展格式规范.md)
- [Changelog](CHANGELOG.md)

## Contributing

Issues and pull requests welcome at the project repository.
