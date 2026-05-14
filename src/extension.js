const vscode = require("vscode")
const fs = require("fs")
const path = require("path")
const abcjs = require("./lib/abcjs")
const abcx = require("./abcx")
const uri = { script: "", styles: "", abcjs: "", abcx: "" }
let ctx, panel, diagnostics, layoutMode = "original", layoutBars = null, previewDocument = null

// abcjs v6.1.9 doesn't recognise !8va(! / !8vb(! as decorations.
// Convert to text annotation form for abcjs rendering/MIDI only;
// stored .abc/.abcx files keep native decorations for round-trip.
const preprocessOttava = (abc) => {
	return abc
		.replace(/!8va\(!/g, '"^8va~"')
		.replace(/!8va\)!/g, '"^~"')
		.replace(/!8vb\(!/g, '"^8vb~"')
		.replace(/!8vb\)!/g, '"^~"')
}

const activate = (context) => {
	ctx = context
	diagnostics = vscode.languages.createDiagnosticCollection("abcx")
	ctx.subscriptions.push(diagnostics)
	registerCommands()
	registerEvents()
	updateDiagnostics(vscode.window.activeTextEditor?.document)
}

const deactivate = () => {}

module.exports = {
	activate,
	deactivate
}

const registerCommands = () => {
	const create = vscode.commands.registerCommand(
		"abc.exportMidi", () => exportMidi()
		)
	ctx.subscriptions.push(create)

	const show = vscode.commands.registerCommand(
		"abc.showPreview", () => showPreview()
	)
	ctx.subscriptions.push(show)

	const smartEnter = vscode.commands.registerCommand(
		"abcx.smartEnter", smartEnterHandler
	)
	ctx.subscriptions.push(smartEnter)
}

const registerEvents = () => {
	ctx.subscriptions.push(
		vscode.workspace.onDidChangeTextDocument((event) => {
			updateDiagnostics(event.document)
			updatePanel(event.document)
		})
	)
	ctx.subscriptions.push(
		vscode.window.onDidChangeActiveTextEditor((editor) => {
			updateDiagnostics(editor?.document)
		})
	)
	ctx.subscriptions.push(
		vscode.workspace.onDidCloseTextDocument((document) => {
			diagnostics.delete(document.uri)
		})
	)
}

const exportMidi = (abc = getAnalyzedContent().abc, sourcePath = getSourceFilePath()) => {
	const file = getFileName(sourcePath)
	const output = getOutputPath(sourcePath, ".mid")
	const midi = Buffer.from(
		abcjs.synth.getMidiFile(
			preprocessOttava(abc), {
				midiOutputType: "binary",
				fileName: file + ".mid"
			}
		)[0]
	)
	writeFile(output, midi)
	return output
}

const exportSvg = (svg, sourcePath = getSourceFilePath()) => {
	if (!svg) throw new Error("No SVG content is available to export.")
	const output = getOutputPath(sourcePath, ".svg")
	const content = svg.startsWith("<?xml") ? svg : `<?xml version="1.0" encoding="UTF-8"?>\n${svg}\n`
	writeFile(output, content)
	return output
}

const exportAbc = (abc, sourcePath = getSourceFilePath()) => {
	const output = getOutputPath(sourcePath, ".abc")
	writeFile(output, abc)
	return output
}

const exportStandardAbc = (sourcePath = getSourceFilePath()) => {
	const document = previewDocument || vscode.window.activeTextEditor?.document
	const source = (document?.getText() || "").replace(/\r\n/g, "\n")
	const standard = abcx.toStandardAbc(source)
	const base = path.basename(sourcePath, path.extname(sourcePath))
	const folder = path.dirname(sourcePath)
	const output = path.resolve(folder, `${base}.std.abc`)
	writeFile(output, standard)
	return output
}

const exportStandardAbcx = (sourcePath = getSourceFilePath()) => {
	const document = previewDocument || vscode.window.activeTextEditor?.document
	const source = (document?.getText() || "").replace(/\r\n/g, "\n")
	const standard = abcx.toStandardAbcx(source)
	const base = path.basename(sourcePath, path.extname(sourcePath))
	const folder = path.dirname(sourcePath)
	const output = path.resolve(folder, `${base}.std.abcx`)
	writeFile(output, standard)
	return output
}

const showPreview = () => {
	initializePanel()
	const document = vscode.window.activeTextEditor?.document
	previewDocument = document || previewDocument
	const analyzed = getAnalyzedContent(document)
	panel.webview.html = getWebviewContent(analyzed, document)
}

const renderPanel = (document) => {
	if (!panel || !document) return
	const analyzed = getAnalyzedContent(document)
	panel.webview.html = getWebviewContent(analyzed, document)
}

const updatePanel = (document) => {
	const activeDocument = vscode.window.activeTextEditor?.document
	if (panel && activeDocument && document && activeDocument.uri.toString() === document.uri.toString() && isAbc(document)) {
		renderPanel(document)
	}
}

const initializePanel = () => {
	panel = vscode.window.createWebviewPanel(
        'abcPreview',
        'Preview',
        vscode.ViewColumn.Beside,
        { enableScripts: true }
	)

	uri.styles = panel.webview.asWebviewUri(
		vscode.Uri.joinPath(ctx.extensionUri, 'webview', 'main.css')
	)
	uri.script = panel.webview.asWebviewUri(
		vscode.Uri.joinPath(ctx.extensionUri, 'webview', 'main.js')
	)
	uri.abcjs = panel.webview.asWebviewUri(
		vscode.Uri.joinPath(ctx.extensionUri, 'src/lib', 'abcjs.js')
	)
	uri.abcx = panel.webview.asWebviewUri(
		vscode.Uri.joinPath(ctx.extensionUri, 'src', 'abcx.js')
	)
	panel.webview.onDidReceiveMessage((message) => {
		handleWebviewMessage(message)
	}, undefined, ctx.subscriptions)
	panel.onDidDispose(() => {
		panel = null
		previewDocument = null
	}, undefined, ctx.subscriptions)
}

const handleWebviewMessage = (message) => {
	try {
		if (!message || !message.type) return
		if (message.type === "exportMidi") {
			const output = exportMidi(message.abc, message.sourcePath)
			vscode.window.showInformationMessage(`MIDI exported to ${output}`)
			return
		}
		if (message.type === "exportSvg") {
			const output = exportSvg(message.svg, message.sourcePath)
			vscode.window.showInformationMessage(`SVG exported to ${output}`)
			return
		}
		if (message.type === "exportAbc") {
			const output = exportAbc(message.abc, message.sourcePath)
			vscode.window.showInformationMessage(`ABC exported to ${output}`)
			return
		}
		if (message.type === "exportStandardAbc") {
			const output = exportStandardAbc(message.sourcePath)
			vscode.window.showInformationMessage(`Standard ABC exported to ${output}`)
			return
		}
		if (message.type === "exportStandardAbcx") {
			const output = exportStandardAbcx(message.sourcePath)
			vscode.window.showInformationMessage(`Standard ABCX exported to ${output}`)
			return
		}
		if (message.type === "layoutChanged") {
			layoutMode = message.mode || "original"
			layoutBars = message.barsPerLine || null
			const document = previewDocument || vscode.window.activeTextEditor?.document
			if (document && isAbc(document)) renderPanel(document)
		}
	} catch (err) {
		vscode.window.showErrorMessage(err && err.message ? err.message : String(err))
	}
}

const isBodyLine = (document, lineIndex) => {
	for (let i = 0; i <= lineIndex; i++) {
		const text = document.lineAt(i).text.trim()
		if (text.startsWith("K:")) return true
		if (text && !text.startsWith("%") && !text.startsWith("%%") && !/^[A-Za-z]:/.test(text) && !/^\[/.test(text)) {
			return true
		}
	}
	return false
}

const findBarPositions = (text) => {
	const bars = []
	let i = 0
	while (i < text.length) {
		if (text[i] === "|") {
			const barStart = i
			i++
			while (i < text.length && /[:|\]\[]/.test(text[i])) i++
			if (i < text.length && /\d/.test(text[i])) i++
			bars.push({ start: barStart, end: i })
		} else {
			i++
		}
	}
	return bars
}

const smartEnterHandler = async () => {
	const editor = vscode.window.activeTextEditor
	if (!editor || !editor.document) return
	const doc = editor.document
	if (doc.languageId !== "abcx" && doc.languageId !== "abc") {
		await vscode.commands.executeCommand("default:type", { text: "\n" })
		return
	}

	const position = editor.selection.active
	const line = doc.lineAt(position.line)
	const lineText = line.text

	if (lineText.trim() === "" || lineText.trim().startsWith("%")) {
		await vscode.commands.executeCommand("default:type", { text: "\n" })
		return
	}

	if (isBodyLine(doc, position.line)) {
		const bars = findBarPositions(lineText)
		if (bars.length > 0) {
			const cursorCol = position.character
			let closestBar = bars[0]
			let bestDistance = Infinity
			for (const bar of bars) {
				const dist = Math.abs(bar.start - cursorCol)
				if (dist < bestDistance) {
					bestDistance = dist
					closestBar = bar
				}
			}

			await editor.edit((editBuilder) => {
				if (cursorCol >= closestBar.end) {
					const indent = lineText.slice(0, closestBar.end).match(/^(\s*)/)[1]
					editBuilder.insert(new vscode.Position(position.line, closestBar.end), `\n${indent}`)
				} else {
					editBuilder.insert(new vscode.Position(position.line, closestBar.start), "\n")
				}
			})
			return
		}
	}

	await vscode.commands.executeCommand("default:type", { text: "\n" })
}

const getEditorContent = (document) => {
	const content = (document || vscode.window.activeTextEditor?.document)?.getText() || ""
	return content.replace(/\r\n/g, "\n")
}

const getAnalyzedContent = (document) => {
	const content = getEditorContent(document)
	// Check aligned format FIRST (before hasAbcxBody, since aligned also has semicolons)
	if (abcx.isAlignedAbcx && abcx.isAlignedAbcx(content)) {
		const result = abcx.analyze(content, { abcjs, layout: { mode: layoutMode, barsPerLine: layoutBars } })
		return result
	}
	if (abcx.hasAbcxBody(content)) {
		const result = abcx.analyze(content, { abcjs, layout: { mode: layoutMode, barsPerLine: layoutBars } })
		result.abc = abcx.normalizeAbc(result.abc)
		return result
	}
	return analyzeAbc(content)
}

const getWebviewContent = (analyzed, document) => {
	const abc = JSON.stringify(analyzed.abc)
	const diagnosticsJson = JSON.stringify(analyzed.diagnostics)
	const sourcePath = JSON.stringify(getSourceFilePath(document))
	const layoutModeJson = JSON.stringify(layoutMode)
	const isAbcx = JSON.stringify(analyzed.isAbcx)
	return `
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Preview</title>
			<link href="${uri.styles}" rel="stylesheet">
			<script src="${uri.abcjs}"></script>
			<script src="${uri.abcx}"></script>
		</head>
		<body>
			<section class="toolbar">
				<section class="toolbar-row">
					<button id="play" class="icon-button" title="Play or pause">&#xea1c;</button>
					<input id="progress" type="range" min="0" max="1000" value="0" step="1" title="Playback position">
					<span id="time">0:00 / 0:00</span>
				</section>
				<section class="toolbar-row">
					<select id="layout" title="Line break mode">
						<option value="original">Original</option>
						<option value="auto">Auto</option>
						<option value="fixed-2">2 bars</option>
						<option value="fixed-3">3 bars</option>
						<option value="fixed-4">4 bars</option>
					</select>
					<button id="export-abc" title="Export converted ABC">ABC</button>
					<button id="export-midi" title="Export MIDI">MID</button>
					<button id="export-svg" title="Export SVG">SVG</button>
					<button id="export-std-abc" title="Export normalized ABC (unified L:)">Std ABC</button>
					<button id="export-std-abcx" title="Export normalized ABCX (unified L:)">Std ABCX</button>
				</section>
			</section>
			<section id="messages"></section>
			<main id="paper"></main>
			<script>
				window.__ABC_PREVIEW__ = {
					abc: ${abc},
					sourcePath: ${sourcePath},
					diagnostics: ${diagnosticsJson},
					layoutMode: ${layoutModeJson},
					isAbcx: ${isAbcx}
				}
			</script>
			<script src="${uri.script}"></script>
		</body>
		</html>
	`
}

const getSourceFilePath = (document) => {
	const filePath = (document || vscode.window.activeTextEditor?.document)?.fileName
	if (!filePath) throw new Error("No source document is available for export.")
	return filePath
}

const getFileName = (sourcePath = getSourceFilePath()) => {
	const filePath = sourcePath
	return path.basename(filePath, path.extname(filePath))
}

const getFolderName = (sourcePath = getSourceFilePath()) => {
	const filePath = sourcePath
	return path.dirname(filePath)
}

const getOutputPath = (sourcePath, extension) => {
	const file = getFileName(sourcePath)
	const folder = getFolderName(sourcePath)
	return path.resolve(folder, file + extension)
}

const isAbc = (document) => {
	const language = (document || vscode.window.activeTextEditor?.document)?.languageId
	return language == "abc" || language == "abcx" || language == "plaintext"
}

const analyzeAbc = (content) => {
	const normalized = abcx.normalizeAbc(content)
	const diagnostics = []
	try {
		const parsed = abcjs.parseOnly(normalized)
		for (const tune of parsed || []) {
			for (const warning of tune.warnings || []) {
				diagnostics.push({ severity: "warning", line: 0, column: 0, message: String(warning) })
			}
		}
	} catch (err) {
		diagnostics.push({ severity: "error", line: 0, column: 0, message: err && err.message ? err.message : String(err) })
	}
	return { abc: normalized, diagnostics, isAbcx: false }
}

const updateDiagnostics = (document) => {
	if (!document || !isAbc(document)) return
	const analyzed = getAnalyzedContent(document)
	const items = analyzed.diagnostics.map((item) => {
		const line = Math.max(0, Math.min(item.line || 0, document.lineCount - 1))
		const textLine = document.lineAt(line)
		const column = Math.max(0, Math.min(item.column || 0, textLine.text.length))
		const range = new vscode.Range(line, column, line, Math.min(textLine.text.length, column + 1))
		const severity = item.severity === "error" ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning
		return new vscode.Diagnostic(range, item.message, severity)
	})
	diagnostics.set(document.uri, items)
}

const openFile = (filename) => {
	try {
		return fs.readFileSync(filename)
	} catch (err) {
		if (err) return console.error(err)
	}
}

const writeFile = (filename, data) => {
	fs.writeFileSync(filename, data)
}
