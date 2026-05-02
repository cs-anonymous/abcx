const abcjs = window.ABCJS
const preview = window.__ABC_PREVIEW__ || { abc: "", diagnostics: [] }
const vscode = acquireVsCodeApi()

const paper = document.querySelector("#paper")
const playButton = document.querySelector("#play")
const exportMidiButton = document.querySelector("#export-midi")
const exportSvgButton = document.querySelector("#export-svg")
const exportAbcButton = document.querySelector("#export-abc")
const progress = document.querySelector("#progress")
const timeLabel = document.querySelector("#time")
const messages = document.querySelector("#messages")
const layoutSelect = document.querySelector("#layout")

let audioContext = null
let synth = null
let timing = null
let visualObj = null
let isReady = false
let isPlaying = false
let isDragging = false
let currentElements = []
let totalMs = 0

if (layoutSelect && preview.layoutMode) {
	const modeValue = preview.layoutMode === "auto" ? "auto" : preview.layoutMode === "fixed" ? "fixed-4" : "original"
	layoutSelect.value = modeValue
}

layoutSelect?.addEventListener("change", () => {
	const value = layoutSelect.value
	let mode = value
	let barsPerLine = null
	if (value.startsWith("fixed-")) {
		mode = "fixed"
		barsPerLine = Number(value.split("-")[1])
	}
	vscode.postMessage({ type: "layoutChanged", mode, barsPerLine })
})

const renderMessages = () => {
	const diagnostics = preview.diagnostics || []
	messages.innerHTML = ""
	if (!diagnostics.length) return

	for (const diagnostic of diagnostics) {
		const item = document.createElement("div")
		item.className = `message ${diagnostic.severity === "error" ? "error" : "warning"}`
		const location = Number.isInteger(diagnostic.line) ? `:${diagnostic.line + 1}` : ""
		item.textContent = `${diagnostic.severity.toUpperCase()}${location} ${diagnostic.message}`
		messages.appendChild(item)
	}
}

const renderScore = () => {
	try {
		const foregroundColor = getComputedStyle(document.body).getPropertyValue("--text").trim()
		const result = abcjs.renderAbc(paper, preview.abc, {
			responsive: "resize",
			add_classes: true,
			foregroundColor
		})
		visualObj = result && result[0]
		if (!visualObj) throw new Error("abcjs did not return a visual object.")
		createCursor()
	} catch (err) {
		const item = document.createElement("div")
		item.className = "message error"
		item.textContent = err && err.message ? err.message : String(err)
		messages.appendChild(item)
		disablePlayer()
	}
}

const createCursor = () => {
	const cursor = document.createElement("div")
	cursor.className = "abcjs-cursor"
	cursor.style.display = "none"
	paper.appendChild(cursor)
}

const disablePlayer = () => {
	playButton.disabled = true
	progress.disabled = true
	exportSvgButton.disabled = true
}

const ensureAudio = async () => {
	if (isReady) return
	audioContext = audioContext || new AudioContext()
	await audioContext.resume()
	synth = new abcjs.synth.CreateSynth()
	await synth.init({
		visualObj,
		audioContext,
		millisecondsPerMeasure: visualObj.millisecondsPerMeasure(),
		options: { onEnded: stop }
	})
	await synth.prime()
	timing = new abcjs.TimingCallbacks(visualObj, {
		eventCallback: onEvent,
		beatCallback: onBeat
	})
	totalMs = timing.lastMoment || (synth.duration ? synth.duration * 1000 : 0)
	timeLabel.textContent = `0:00 / ${formatTime(totalMs)}`
	isReady = true
}

const play = async () => {
	await ensureAudio()
	if (isPlaying) return
	await synth.start()
	timing.start(currentPercent())
	isPlaying = true
	playButton.innerText = "\uea1e"
}

const pause = async () => {
	if (!isReady || !isPlaying) return
	await synth.pause()
	timing.pause()
	isPlaying = false
	playButton.innerText = "\uea1c"
}

const stop = async () => {
	if (timing) timing.stop()
	if (synth) await synth.stop()
	isPlaying = false
	playButton.innerText = "\uea1c"
	setProgress(0)
	clearHighlight()
	const cursor = document.querySelector(".abcjs-cursor")
	if (cursor) cursor.style.display = "none"
}

const seek = async (percent) => {
	await ensureAudio()
	const clamped = Math.max(0, Math.min(1, percent))
	synth.seek(clamped)
	timing.setProgress(clamped)
	setProgress(clamped)
	if (isPlaying) timing.start(clamped)
}

const onBeat = (_beat, _totalBeats, lastMoment, _position, debug) => {
	if (isDragging) return
	totalMs = lastMoment || totalMs
	const ms = debug && debug.timestamp && debug.startTime ? debug.timestamp - debug.startTime : timing.currentMillisecond()
	setProgress(totalMs ? ms / totalMs : 0)
}

const onEvent = (event) => {
	if (!event) {
		stop()
		return
	}
	showCursor(event)
	highlightEvent(event)
}

const showCursor = (event) => {
	const cursor = document.querySelector(".abcjs-cursor")
	if (!cursor || event.left == null) return
	cursor.style.display = "block"
	cursor.style.left = `${event.left}px`
	cursor.style.top = `${event.top}px`
	cursor.style.width = `${Math.max(2, event.width)}px`
	cursor.style.height = `${event.height}px`
}

const highlightEvent = (event) => {
	clearHighlight()
	for (const group of event.elements || []) {
		for (const element of group || []) {
			element.classList.add("abcjs-note_playing")
			currentElements.push(element)
		}
	}
	if (currentElements[0]) {
		currentElements[0].scrollIntoView({ block: "center", inline: "center", behavior: "smooth" })
	}
}

const clearHighlight = () => {
	for (const element of currentElements) {
		element.classList.remove("abcjs-note_playing")
	}
	currentElements = []
}

const setProgress = (percent) => {
	const clamped = Math.max(0, Math.min(1, percent || 0))
	progress.value = String(Math.round(clamped * Number(progress.max)))
	timeLabel.textContent = `${formatTime(totalMs * clamped)} / ${formatTime(totalMs)}`
}

const currentPercent = () => {
	return Number(progress.value) / Number(progress.max)
}

const formatTime = (ms) => {
	const seconds = Math.max(0, Math.floor((ms || 0) / 1000))
	const minutes = Math.floor(seconds / 60)
	return `${minutes}:${String(seconds % 60).padStart(2, "0")}`
}

const exportMidi = () => {
	vscode.postMessage({
		type: "exportMidi",
		abc: preview.abc,
		sourcePath: preview.sourcePath
	})
}

const exportSvg = () => {
	const svg = paper.querySelector("svg")
	if (!svg) return
	const clone = svg.cloneNode(true)
	clone.setAttribute("xmlns", "http://www.w3.org/2000/svg")
	clone.setAttribute("color", getComputedStyle(document.body).getPropertyValue("--text").trim())
	vscode.postMessage({
		type: "exportSvg",
		sourcePath: preview.sourcePath,
		svg: clone.outerHTML
	})
}

const exportAbc = () => {
	vscode.postMessage({
		type: "exportAbc",
		abc: preview.abc,
		sourcePath: preview.sourcePath
	})
}

playButton.addEventListener("click", async () => {
	if (isPlaying) {
		await pause()
	} else {
		await play()
	}
})

exportMidiButton.addEventListener("click", exportMidi)
exportSvgButton.addEventListener("click", exportSvg)
if (exportAbcButton) exportAbcButton.addEventListener("click", exportAbc)

progress.addEventListener("input", () => {
	isDragging = true
	setProgress(currentPercent())
})

progress.addEventListener("change", async () => {
	const percent = currentPercent()
	isDragging = false
	await seek(percent)
})

renderMessages()
renderScore()
