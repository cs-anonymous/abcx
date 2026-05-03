# ABCX Tools

An extension to preview and play music written in ABC and ABCX notation inside Visual Studio Code. It's powered by the [abcjs](https://www.abcjs.net/) library.

You can learn more about ABC on its [official website](https://abcnotation.com/).

## Screenshots
Dark Theme
![Dark+ Theme](https://raw.githubusercontent.com/ishiharaf/abc/main/media/dark+.png)

Light+ Theme
![Light+ Theme](https://raw.githubusercontent.com/ishiharaf/abc/main/media/light+.png)

## Usage

Open the command palette and type `ABC` to search for the `ABC: Show Preview` command. It'll open a new panel with a preview of the ABC or ABCX file. Clicking on the `⏵` icon will play the file. The preview includes a draggable playback progress bar and highlights the current note while playing. `ABC: Export MIDI` will export a MIDI file in the current file directory. You can also click the buttons in the editor to call these commands.

ABCX files use the `.abcx` extension. The extension converts ABCX to standard ABC before sending it to abcjs, so rendering, playback, and MIDI export share the same output.

The ABCX linter checks:

- `%%score` voice declarations.
- `;` voice count per measure.
- Strict per-measure duration matching for each voice and `&` layer.
- Explicit range marker pairing such as `@[V1:c1:crescendo(` and `@[V1:c1:crescendo)`.
- abcjs parse warnings after conversion.

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
npx vsce package --no-dependencies -o abcx-tools-0.3.0.vsix

# 安装到当前 VS Code（含远程 SSH 主机）
code --install-extension /home/sy/2026/Music/EPR/abcx/abcx-tools-0.3.0.vsix --force
```