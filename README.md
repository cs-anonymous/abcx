# ABCX Tools

An extension to preview and play music written in ABC and ABCX notation inside Visual Studio Code. It's powered by the [abcjs](https://www.abcjs.net/) library.

**Now with Aligned ABCX Format Support!** 🎵

You can learn more about ABC on its [official website](https://abcnotation.com/).

## Screenshots
Dark Theme
![Dark+ Theme](https://raw.githubusercontent.com/ishiharaf/abc/main/media/dark+.png)

Light+ Theme
![Light+ Theme](https://raw.githubusercontent.com/ishiharaf/abc/main/media/light+.png)

## Usage

Open the command palette and type `ABC` to search for the `ABC: Show Preview` command. It'll open a new panel with a preview of the ABC, ABCX, or ABCI file. Clicking on the `⏵` icon will play the file. The preview includes a draggable playback progress bar and highlights the current note while playing. `ABC: Export MIDI` will export a MIDI file in the current file directory. `ABC: Convert to ABCX` will write a normalized `.abcx` file next to the current `.abc` or `.abci` source. You can also click the buttons in the editor to call these commands.

### Standard ABCX Format

ABCX files use the `.abcx` extension. The extension converts ABCX to standard ABC before sending it to abcjs, so rendering, playback, and MIDI export share the same output.

The ABCX linter checks:

- `%%score` voice declarations.
- `;` voice count per measure.
- Strict per-measure duration matching for each voice and `&` layer.
- Explicit range marker pairing such as `@[V1:c1:crescendo(` and `@[V1:c1:crescendo)`.
- abcjs parse warnings after conversion.

### Aligned ABCX Format

The plugin supports **aligned ABCX format**, a phrase-aligned notation format projected to two output staves. This format uses:

- **Phrase markers** in either legacy or token form:
  - `H1`, `H2`, ...
  - `<H><V000>`, `<H><V001>`, ...
- **Measure markers** in either legacy or token form:
  - `M1`, `M2`, ...
  - `<M><V000>`, `<M><V001>`, ...
- **Whitespace after the measure marker** followed by staff content with exactly one `;` separator:
  - `M1 StaffU ; StaffL`
  - `<M><V000>\tStaffU ; StaffL`
- `&` to join multiple voices within the same staff
- `.` as an empty-staff placeholder; preview/export converts it to a full-measure rest

Example:
```abcx
X:1
T:Example
L:1/16
M:2/4
K:C
<H><V000>
<M><V000>	C2D2 & E2F2 ; C,2D,2
<M><V001>	. ; G,,8
```

For complete documentation, see [ALIGNED_FORMAT.md](ALIGNED_FORMAT.md).

The bundled `.abci` dataset files are treated as ABCX-like source files and can be converted with `ABC: Convert to ABCX`.

Snippets are available to aid with the creation of new files. Type `ABC` and select one of the snippets. For example, this is the `ABC: Headers (Minimal)`:

```
X:1
T:Title
K:C
z4
```

It's the bare minimum an ABC file must have to be valid.

## Changelog

See the [changelog](CHANGELOG.md) file.

```
# 打包（在插件根目录运行）
cd /home/sy/2026/Music/EPR/abcx
npx vsce package --no-dependencies -o abcx-tools-0.3.5.vsix

# 安装到当前 VS Code（含远程 SSH 主机）
code --install-extension /home/sy/2026/Music/EPR/abcx/abcx-tools-0.3.5.vsix --force
```
