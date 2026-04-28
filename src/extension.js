const vscode = require("vscode")
const fs = require("fs")
const path = require("path")
const abcjs = require("./lib/abcjs")
const abcx = require("./abcx")
const uri = { script: "", styles: "", abcjs: "", abcx: "" }
let ctx, panel, diagnostics

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

const exportMidi = () => {
	const file = getFileName(),
		  output = getMidiPath() + ".mid"
	const abc = getAnalyzedContent().abc
	const midi = Buffer.from(
		abcjs.synth.getMidiFile(
			abc, {
				midiOutputType: "binary",
				fileName: file + ".mid"
			}
		)[0]
	)
	writeFile(output, midi)
}

const showPreview = () => {
	initializePanel()
	const analyzed = getAnalyzedContent()
	panel.webview.html = getWebviewContent(analyzed)
}

const updatePanel = (document) => {
	const activeDocument = vscode.window.activeTextEditor?.document
	if (panel && activeDocument && document && activeDocument.uri.toString() === document.uri.toString() && isAbc(document)) {
		const analyzed = getAnalyzedContent(document)
		panel.webview.html = getWebviewContent(analyzed)
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
}

const getEditorContent = (document) => {
	const content = (document || vscode.window.activeTextEditor?.document)?.getText() || ""
	return content.replace(/\r\n/g, "\n")
}

const getAnalyzedContent = (document) => {
	const content = getEditorContent(document)
	if (abcx.isAbcx(content)) return abcx.analyze(content, { abcjs })
	return analyzeAbc(content)
}

const getWebviewContent = (analyzed) => {
	const abc = JSON.stringify(analyzed.abc)
	const diagnosticsJson = JSON.stringify(analyzed.diagnostics)
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
				<button id="play" title="Play or pause">&#xea1c;</button>
				<input id="progress" type="range" min="0" max="1000" value="0" step="1" title="Playback position">
				<span id="time">0:00 / 0:00</span>
			</section>
			<section id="messages"></section>
			<main id="paper"></main>
			<script>
				window.__ABC_PREVIEW__ = {
					abc: ${abc},
					diagnostics: ${diagnosticsJson}
				}
			</script>
			<script src="${uri.script}"></script>
		</body>
		</html>
	`
}

const getFileName = () => {
	const path = vscode.window.activeTextEditor.document.fileName.split("\\")
	return path[path.length - 1].split(".")[0]
}

const getFolderName = () => {
	const file = getFileName()
	const filePath = vscode.window.activeTextEditor.document.fileName
	return path.dirname(filePath)
}

const getMidiPath = () => {
	const file = getFileName()
	const folder = getFolderName()
	return path.resolve(folder, file)
}

const isAbc = (document) => {
	const language = (document || vscode.window.activeTextEditor?.document)?.languageId
	return language == "abc" || language == "abcx" || language == "plaintext"
}

const analyzeAbc = (content) => {
	const diagnostics = []
	try {
		const parsed = abcjs.parseOnly(content)
		for (const tune of parsed || []) {
			for (const warning of tune.warnings || []) {
				diagnostics.push({ severity: "warning", line: 0, column: 0, message: String(warning) })
			}
		}
	} catch (err) {
		diagnostics.push({ severity: "error", line: 0, column: 0, message: err && err.message ? err.message : String(err) })
	}
	return { abc: content, diagnostics, isAbcx: false }
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
	fs.writeFile(filename, data, err => {
		if (err) return console.error(err)
	})
}
