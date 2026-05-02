(function (root, factory) {
	if (typeof module === "object" && module.exports) {
		module.exports = factory()
	} else {
		root.ABCX = factory()
	}
})(typeof self !== "undefined" ? self : this, function () {
	const EPSILON = 1 / 1000000

	const fieldRe = /^[A-Za-z]:/

	const isAbcx = (source) => {
		return /^\s*%%score\s+/m.test(source) || /(^|\n)[^\n%]*;/.test(source) || /(^|\s)@\[/.test(source)
	}

	const analyze = (source, options = {}) => {
		const normalized = (source || "").replace(/\r\n/g, "\n")
		const state = parsePrelude(normalized)
		const diagnostics = []

		if (!state.scoreLine && isAbcx(normalized)) {
			addDiagnostic(diagnostics, "error", 0, 0, "ABCX requires a %%score voice/staff declaration.")
		}

		const voices = state.voices.length ? state.voices : inferVoicesFromBody(state.bodyLines)
		const meter = parseMeter(state.fields.M || "4/4")
		const defaultLength = parseFraction(state.fields.L || "1/8")
		const layout = options.layout || { mode: "original" }

		validateRanges(state.bodyLines, diagnostics)

		let barsPerLine = null
		if (layout.mode === "fixed") {
			barsPerLine = layout.barsPerLine || 4
		}

		const linebreakChar = layout.mode === "auto" || layout.mode === "fixed" ? "$" : ""

		// auto: max notes per line = ~3 bars worth (in defaultLength units)
		const maxNotesPerLine = layout.mode === "auto"
			? Math.round(meter / defaultLength) * 3
			: null

		const bodyResult = convertBody(state.bodyLines, voices, {
			diagnostics,
			meter,
			defaultLength
		})

		const abc = buildAbc(state, voices, bodyResult, linebreakChar, layout, {
			barsPerLine,
			maxNotesPerLine
		})

		if (options.abcjs) {
			try {
				const parsed = options.abcjs.parseOnly(abc)
				for (const tune of parsed || []) {
					for (const warning of tune.warnings || []) {
						addDiagnostic(diagnostics, "warning", 0, 0, cleanAbcjsWarning(warning))
					}
				}
			} catch (err) {
				addDiagnostic(diagnostics, "error", 0, 0, err && err.message ? err.message : String(err))
			}
		}

		return {
			abc,
			diagnostics,
			isAbcx: isAbcx(normalized),
			voices,
			meter,
			defaultLength
		}
	}

	const parsePrelude = (source) => {
		const lines = source.split("\n")
		const prelude = []
		const bodyLines = []
		const fields = {}
		let scoreLine = null
		let inBody = false

		for (let index = 0; index < lines.length; index++) {
			const text = lines[index]
			const trimmed = text.trim()
			const isPreludeLine = !trimmed || trimmed.startsWith("%") || trimmed.startsWith("%%") || fieldRe.test(trimmed)

			if (!inBody && isPreludeLine) {
				prelude.push({ text, line: index })
				if (trimmed.startsWith("%%score")) scoreLine = { text, line: index }
				const field = trimmed.match(/^([A-Za-z]):\s*(.*)$/)
				if (field) fields[field[1]] = field[2]
			} else {
				inBody = true
				bodyLines.push({ text, line: index })
			}
		}

		return {
			lines,
			prelude,
			bodyLines,
			fields,
			scoreLine,
			voices: scoreLine ? parseScoreVoices(scoreLine.text) : []
		}
	}

	const parseScoreVoices = (score) => {
		const voices = []
		const seen = new Set()
		const matches = score.matchAll(/\(([^)]*)\)/g)
		for (const match of matches) {
			const names = match[1].trim().split(/\s+/).filter(Boolean)
			for (const name of names) {
				const normalized = normalizeVoiceName(name)
				if (!seen.has(normalized)) {
					seen.add(normalized)
					voices.push(normalized)
				}
			}
		}
		return voices
	}

	const inferVoicesFromBody = (bodyLines) => {
		let count = 1
		for (const line of bodyLines) {
			const parts = splitTopLevel(stripComment(line.text), ";")
			if (parts.length > count) count = parts.length
		}
		return Array.from({ length: count }, (_, index) => `V${index + 1}`)
	}

	const normalizeVoiceName = (name) => {
		const match = String(name).trim().match(/^v?(\d+)$/i)
		return match ? `V${match[1]}` : String(name).trim()
	}

	const buildAbc = (state, voices, bodyResult, linebreakChar, layout, layoutOpts) => {
		const { lines: bodyLines, notesPerLine } = bodyResult
		const { barsPerLine, maxNotesPerLine } = layoutOpts || {}
		const header = []
		const voiceDefinitions = new Set()
		let keyLine = null
		let hasScore = false

		for (const item of state.prelude) {
			const trimmed = item.text.trim()
			if (!trimmed) continue
			if (trimmed.startsWith("%%score")) {
				header.push(item.text)
				hasScore = true
				continue
			}
			if (/^K:/.test(trimmed)) {
				keyLine = item.text
				continue
			}
			const voice = item.text.match(/^(\s*)V:\s*([^\s]+)(.*)$/)
			if (voice) {
				const normalized = normalizeVoiceName(voice[2])
				voiceDefinitions.add(normalized)
				header.push(`${voice[1]}V:${stripLeadingV(normalized)}${voice[3]}`)
				continue
			}
			header.push(item.text)
		}

		if (!hasScore && voices.length > 1) {
			header.push(`%%score ${voices.map((voice) => `(${voice})`).join(" ")}`)
		}

		for (const voice of voices) {
			if (!voiceDefinitions.has(voice)) {
				header.push(`V:${stripLeadingV(voice)}`)
			}
		}

		if (keyLine) header.push(keyLine)

		const needsLinebreak = true
		header.push("I:linebreak $")

		// collect measure content: group [V:voice] lines per voice
		const voiceAccum = voices.map(() => [])
		const passThroughs = [] // { voiceIndex, content }
		for (const line of bodyLines) {
			const m = line.match(/^\[V:([^\]]+)\]\s*(.*)$/)
			if (m) {
				const voiceName = normalizeVoiceName(m[1])
				const idx = voices.indexOf(voiceName)
				if (idx >= 0) voiceAccum[idx].push(m[2].trim())
			} else if (line.trim()) {
				// pass-through: comments, %% directives → first voice
				passThroughs.push({ index: voiceAccum[0].length, content: line })
				voiceAccum[0].push(line)
			}
		}

		const bodyParts = []
		const groupsPerVoice = voices.map((voice, v) => {
			if (layout && layout.mode !== "original") {
				return mergeBars(voiceAccum[v], layout, barsPerLine, maxNotesPerLine)
			}
			return voiceAccum[v]
		})

		const groupSize = layout && layout.mode === "fixed" && barsPerLine ? barsPerLine : 1
		const maxGroups = Math.max(...groupsPerVoice.map((g) => g.length), 0)

		for (let g = 0; g < maxGroups; g++) {
			for (let v = 0; v < voices.length; v++) {
				const groupLine = groupsPerVoice[v][g]
				if (groupLine !== undefined) {
					const voiceName = stripLeadingV(voices[v])
					const isLastVoice = v === voices.length - 1
					const linebreak = needsLinebreak && isLastVoice ? "$" : ""
					bodyParts.push(`[V:${voiceName}] ${barSuffix(groupLine)}${linebreak}`)
				}
			}
		}

		return `${header.join("\n")}\n${bodyParts.join("\n")}`.trimEnd() + "\n"
	}

	const stripLeadingV = (name) => String(name).replace(/^V(\d)/, "$1")

	const barSuffix = (content) => /[\]:|]$/.test(content.trim()) ? content.trim() : content.trim() + "|"

	const mergeBars = (bars, layout, barsPerLine, maxNotesPerLine) => {
		if (layout.mode === "fixed" && barsPerLine !== null) {
			const result = []
			for (let i = 0; i < bars.length; i += barsPerLine) {
				const group = bars.slice(i, i + barsPerLine)
				result.push(group.join("|"))
			}
			return result
		}

		if (layout.mode === "auto" && maxNotesPerLine !== null) {
			const result = []
			let current = ""
			let currentNotes = 0
			for (const bar of bars) {
				const barNotes = countBarNotes(bar)
				if (current && currentNotes + barNotes > maxNotesPerLine) {
					result.push(current)
					current = bar
					currentNotes = barNotes
				} else if (current) {
					current += "|" + bar
					currentNotes += barNotes
				} else {
					current = bar
					currentNotes = barNotes
				}
			}
			if (current) result.push(current)
			return result
		}

		return [bars.join("|")]
	}

	const countBarNotes = (bar) => {
		const text = bar.replace(/\|[\]:|[]*/g, "")
			.replace(/"[^"]*"/g, "")
			.replace(/![^!]*!/g, "")
			.replace(/\{[^}]*\}/g, "")
			.replace(/\[[A-Za-z]:[^\]]*\]/g, "")
		const tokens = text.trim().split(/\s+/).filter(Boolean)
		return tokens.length || 1
	}


	const convertBody = (bodyLines, voices, context) => {
		const outputLines = []
		const notesPerLine = []

		let currentMeter = context.meter
		let currentDefaultLength = context.defaultLength

		const meterRe = /\[M:\s*([A-Za-z0-9/|]+)\]/g
		const lengthRe = /\[L:\s*(\d+\/\d+)\]/g

		for (const line of bodyLines) {
			const text = line.text
			if (!text.trim()) {
				outputLines.push("")
				notesPerLine.push(0)
				continue
			}
			if (/^\s*%/.test(text) || fieldRe.test(text.trim()) || /^\s*\[[A-Za-z]:/.test(text)) {
				outputLines.push(text)
				notesPerLine.push(0)
				let m
				meterRe.lastIndex = 0
				while ((m = meterRe.exec(text))) currentMeter = parseFraction(m[1])
				lengthRe.lastIndex = 0
				while ((m = lengthRe.exec(text))) currentDefaultLength = parseFraction(m[1])
				continue
			}

			const measures = splitMeasures(text)
			if (!measures.length) {
				outputLines.push(text)
				notesPerLine.push(0)
				continue
			}

			const perVoice = voices.map(() => "")
			let lineNotes = 0
			for (const measure of measures) {
				const parts = splitTopLevel(measure.content, ";")

				// extract [M:...] and [L:...] from first voice only
				if (parts.length > 0) {
					let m
					meterRe.lastIndex = 0
					while ((m = meterRe.exec(parts[0]))) currentMeter = parseFraction(m[1])
					lengthRe.lastIndex = 0
					while ((m = lengthRe.exec(parts[0]))) currentDefaultLength = parseFraction(m[1])
				}

				if (parts.length !== voices.length) {
					addDiagnostic(
						context.diagnostics,
						"error",
						line.line,
						measure.column,
						`Expected ${voices.length} voice(s) from %%score, found ${parts.length}.`
					)
				}
				for (let index = 0; index < voices.length; index++) {
					const content = parts[index] == null ? "" : parts[index]
					validateMeasureDuration(content, currentMeter, currentDefaultLength, context.diagnostics, line.line, measure.column, voices[index])
					perVoice[index] += `${measure.prefix}${convertVoiceContent(content)}${measure.suffix}`
				}
				// count notes in the first voice for this measure
				const firstVoiceContent = parts[0] != null ? parts[0] : ""
				lineNotes += countNotes(firstVoiceContent, currentDefaultLength)
			}

			for (let index = 0; index < voices.length; index++) {
				outputLines.push(`[V:${voices[index]}] ${perVoice[index].trim()}`)
			}
			notesPerLine.push(lineNotes)
		}
		return { lines: outputLines, notesPerLine }
	}

	// count notes (in eighth-note units) in a measure content string
	const splitMeasures = (line) => {
		const result = []
		let prefix = ""
		let content = ""
		let contentStart = 0
		let index = 0
		while (index < line.length) {
			if (line[index] === "|") {
				const barStart = index
				let bar = line[index++]
				while (index < line.length && /[:|\]\[]/.test(line[index])) {
					bar += line[index++]
				}
				if (index < line.length && /\d/.test(line[index])) {
					bar += line[index++]
				}
				if (!content.trim() && !prefix) {
					prefix = bar
					contentStart = index
				} else {
					result.push({ prefix, content, suffix: bar, column: contentStart })
					prefix = ""
					content = ""
					contentStart = index
				}
			} else {
				if (!content) contentStart = index
				content += line[index++]
			}
		}
		if (content.trim()) {
			result.push({ prefix, content, suffix: "", column: contentStart })
		}
		return result
	}

	const convertVoiceContent = (content) => {
		return stripExplicitRanges(content).trim()
	}

	const validateMeasureDuration = (content, meter, defaultLength, diagnostics, line, column, voice) => {
		const layers = splitTopLevel(content, "&")
		for (let layerIndex = 0; layerIndex < layers.length; layerIndex++) {
			const layer = layers[layerIndex]
			const duration = measureDuration(layer, defaultLength)
			if (duration === 0 && !layer.trim()) {
				addDiagnostic(diagnostics, "error", line, column, `${voice}.${layerIndex + 1} is empty; write an explicit rest.`)
				continue
			}
			if (Math.abs(duration - meter) > EPSILON) {
				addDiagnostic(
					diagnostics,
					"error",
					line,
					column,
					`${voice}.${layerIndex + 1} duration is ${formatFraction(duration)}, expected ${formatFraction(meter)}.`
				)
			}
		}
	}

	const validateRanges = (bodyLines, diagnostics) => {
		const open = new Map()
		const rangeRe = /@\[([A-Za-z0-9_.]+):([A-Za-z0-9_-]+):([A-Za-z0-9_-]+)([()])/g
		for (const line of bodyLines) {
			let match
			while ((match = rangeRe.exec(line.text))) {
				const key = `${match[1]}:${match[2]}:${match[3]}`
				if (match[4] === "(") {
					if (open.has(key)) {
						addDiagnostic(diagnostics, "error", line.line, match.index, `Range marker ${key} is already open.`)
					}
					open.set(key, { line: line.line, column: match.index })
				} else if (!open.has(key)) {
					addDiagnostic(diagnostics, "error", line.line, match.index, `Range marker ${key} closes before it opens.`)
				} else {
					open.delete(key)
				}
			}
		}
		for (const [key, location] of open.entries()) {
			addDiagnostic(diagnostics, "error", location.line, location.column, `Range marker ${key} is not closed.`)
		}
	}

	const measureDuration = (source, defaultLength) => {
		let text = stripExplicitRanges(stripComment(source))
		text = text.replace(/"[^"]*"/g, "")
		text = text.replace(/![^!]*!/g, "")
		text = text.replace(/\[[A-Za-z]:[^\]]*\]/g, "")
		text = text.replace(/\{[^}]*\}/g, "")

		let index = 0
		let total = 0
		let tuplet = null

		while (index < text.length) {
			const char = text[index]
			if (/\s|[()<>.-]/.test(char)) {
				if (char === "(") {
					const tupletMatch = text.slice(index).match(/^\((\d+)(?::\d+)?(?::\d+)?/)
					if (tupletMatch) {
						const count = Number(tupletMatch[1])
						tuplet = { remaining: count, multiplier: tupletMultiplier(count) }
						index += tupletMatch[0].length
						continue
					}
				}
				index++
				continue
			}

			let duration = 0
			if (char === "[") {
				const end = text.indexOf("]", index + 1)
				if (end === -1) break
				const chord = text.slice(index, end + 1)
				if (/[A-Ga-gxz]/.test(chord)) {
					const parsed = parseDurationSuffix(text, end + 1, defaultLength)
					duration = parsed.duration
					index = parsed.index
				} else {
					index = end + 1
				}
			} else {
				const noteMatch = text.slice(index).match(/^(?:\^{1,2}|_{1,2}|=)?[A-Ga-gxz][,']*/)
				if (noteMatch) {
					const parsed = parseDurationSuffix(text, index + noteMatch[0].length, defaultLength)
					duration = parsed.duration
					index = parsed.index
				} else {
					index++
				}
			}

			if (duration > 0) {
				if (tuplet) {
					duration *= tuplet.multiplier
					tuplet.remaining--
					if (tuplet.remaining <= 0) tuplet = null
				}
				total += duration
			}
		}
		return total
	}

	const parseDurationSuffix = (text, index, defaultLength) => {
		const match = text.slice(index).match(/^(\d+)?(\/+)?(\d+)?/)
		let multiplier = 1
		if (match && match[0]) {
			const number = match[1] ? Number(match[1]) : 1
			if (match[2]) {
				const denominator = match[3] ? Number(match[3]) : Math.pow(2, match[2].length)
				multiplier = number / denominator
			} else {
				multiplier = number
			}
			index += match[0].length
		}
		return { duration: defaultLength * multiplier, index }
	}

	const tupletMultiplier = (count) => {
		if (count === 2) return 3 / 2
		if (count === 3) return 2 / 3
		if (count === 4) return 3 / 4
		return (count - 1) / count
	}

	const countNotes = (content, defaultLength) => {
		let text = stripExplicitRanges(stripComment(content))
		text = text.replace(/"[^"]*"/g, "")
		text = text.replace(/![^!]*!/g, "")
		text = text.replace(/\[[A-Za-z]:[^\]]*\]/g, "")
		text = text.replace(/\{[^}]*\}/g, "")

		let total = 0
		let index = 0
		let tuplet = null
		while (index < text.length) {
			const char = text[index]
			if (/\s|[()<>.\-]/.test(char)) {
				if (char === "(") {
					const tupletMatch = text.slice(index).match(/^\((\d+)(?::\d+)?(?::\d+)?/)
					if (tupletMatch) {
						tuplet = { remaining: Number(tupletMatch[1]), multiplier: tupletMultiplier(Number(tupletMatch[1])) }
						index += tupletMatch[0].length
						continue
					}
				}
				index++
				continue
			}
			let duration = 0
			if (char === "[") {
				const end = text.indexOf("]", index + 1)
				if (end === -1) break
				const chord = text.slice(index, end + 1)
				if (/[A-Ga-gxz]/.test(chord)) {
					const parsed = parseDurationSuffix(text, end + 1, defaultLength)
					duration = parsed.duration
					index = parsed.index
				} else {
					index = end + 1
				}
			} else {
				const noteMatch = text.slice(index).match(/^(?:\^{1,2}|_{1,2}|=)?[A-Ga-gxz][,']*/)
				if (noteMatch) {
					const parsed = parseDurationSuffix(text, index + noteMatch[0].length, defaultLength)
					duration = parsed.duration
					index = parsed.index
				} else {
					index++
				}
			}
			if (duration > 0) {
				if (tuplet) {
					duration *= tuplet.multiplier
					tuplet.remaining--
					if (tuplet.remaining <= 0) tuplet = null
				}
				total += duration / defaultLength // normalize to eighth-note units
			}
		}
		return Math.round(total)
	}

	const splitTopLevel = (source, delimiter) => {
		const parts = []
		let current = ""
		let quote = false
		let bracket = 0
		let brace = 0
		for (let index = 0; index < source.length; index++) {
			const range = source.slice(index).match(/^@\[([A-Za-z0-9_.]+):([A-Za-z0-9_-]+):([A-Za-z0-9_-]+)[()]/)
			if (!quote && range) {
				current += range[0]
				index += range[0].length - 1
				continue
			}
			const char = source[index]
			if (char === "\"") quote = !quote
			if (!quote) {
				if (char === "[") bracket++
				if (char === "]" && bracket) bracket--
				if (char === "{") brace++
				if (char === "}" && brace) brace--
			}
			if (char === delimiter && !quote && bracket === 0 && brace === 0) {
				parts.push(current)
				current = ""
			} else {
				current += char
			}
		}
		parts.push(current)
		return parts
	}

	const stripComment = (line) => {
		let quote = false
		for (let index = 0; index < line.length; index++) {
			if (line[index] === "\"") quote = !quote
			if (line[index] === "%" && !quote) return line.slice(0, index)
		}
		return line
	}

	const stripExplicitRanges = (source) => {
		return source.replace(/@\[([A-Za-z0-9_.]+):([A-Za-z0-9_-]+):([A-Za-z0-9_-]+)([()])/g, (all, scope, id, kind, paren) => {
			const decoration = rangeDecoration(kind, paren)
			return decoration || ""
		})
	}

	const rangeDecoration = (kind, paren) => {
		const map = {
			crescendo: ["!crescendo(!", "!crescendo)!"],
			diminuendo: ["!diminuendo(!", "!diminuendo)!"],
			pedal: ["!ped!", "!ped-up!"],
			ottava8va: ["!8va(!", "!8va)!"],
			ottava8vb: ["!8vb(!", "!8vb)!"],
			trill: ["!trill(!", "!trill)!"],
			slur: ["(", ")"],
			phrase: ["(", ")"]
		}
		const pair = map[kind]
		if (!pair) return ""
		return paren === "(" ? pair[0] : pair[1]
	}

	const parseMeter = (value) => {
		const trimmed = String(value || "").trim()
		if (trimmed === "C") return 1
		if (trimmed === "C|") return 1
		return parseFraction(trimmed || "4/4")
	}

	const parseFraction = (value) => {
		const match = String(value).trim().match(/^(\d+)\s*\/\s*(\d+)/)
		if (!match) return 1 / 8
		return Number(match[1]) / Number(match[2])
	}

	const formatFraction = (value) => {
		const denominators = [1, 2, 4, 8, 16, 32, 64]
		for (const denominator of denominators) {
			const numerator = Math.round(value * denominator)
			if (Math.abs(value - numerator / denominator) < EPSILON) return `${numerator}/${denominator}`
		}
		return String(Number(value.toFixed(6)))
	}

	const addDiagnostic = (diagnostics, severity, line, column, message) => {
		diagnostics.push({ severity, line, column, message })
	}

	const cleanAbcjsWarning = (warning) => {
		return String(warning)
			.replace(/^Music Line:\d+:\d+:\s*/, "")
			.replace(/<[^>]+>/g, "")
	}

	const abcxToAbc = (source) => {
		if (!isAbcx(source)) return source
		return analyze(source).abc
	}

	const gcdInt = (a, b) => {
		a = Math.abs(a); b = Math.abs(b)
		while (b) { const t = b; b = a % b; a = t }
		return a || 1
	}

	const formatMultiplier = (num, den) => {
		const g = gcdInt(num, den)
		const n = Math.round(num / g)
		const d = Math.round(den / g)
		if (d === 1) return n === 1 ? "" : String(n)
		if (n === 1 && d === 2) return "/"
		if (n === 1) return `/${d}`
		return `${n}/${d}`
	}

	const rewriteDurations = (content, factorNum, factorDen) => {
		if (factorNum === factorDen) return content
		let result = ""
		let i = 0
		const len = content.length

		const consumeDur = (start) => {
			const m = content.slice(start).match(/^(\d+)?(\/+)?(\d+)?/)
			if (!m || !m[0]) return { num: 1, den: 1, end: start }
			let num = 1, den = 1
			if (m[1]) num = parseInt(m[1], 10)
			if (m[2]) {
				den = m[3] ? parseInt(m[3], 10) : Math.pow(2, m[2].length)
			}
			return { num, den, end: start + m[0].length }
		}

		const skipPaired = (openIdx, closeCh) => {
			const end = content.indexOf(closeCh, openIdx + 1)
			return end < 0 ? len : end + 1
		}

		while (i < len) {
			const ch = content[i]

			if (ch === "\"") { const e = skipPaired(i, "\""); result += content.slice(i, e); i = e; continue }
			if (ch === "!") { const e = skipPaired(i, "!"); result += content.slice(i, e); i = e; continue }
			if (ch === "{") { const e = skipPaired(i, "}"); result += content.slice(i, e); i = e; continue }

			if (ch === "[" && /^[A-Za-z]:/.test(content.slice(i + 1, i + 3))) {
				const e = skipPaired(i, "]")
				result += content.slice(i, e)
				i = e
				continue
			}
			if (ch === "[") {
				const e = skipPaired(i, "]")
				result += content.slice(i, e)
				i = e
				const d = consumeDur(i)
				result += formatMultiplier(d.num * factorNum, d.den * factorDen)
				i = d.end
				continue
			}

			const noteMatch = content.slice(i).match(/^((?:\^{1,2}|_{1,2}|=)?)([A-Ga-gxyz])([,']*)/)
			if (noteMatch) {
				result += noteMatch[0]
				i += noteMatch[0].length
				const d = consumeDur(i)
				result += formatMultiplier(d.num * factorNum, d.den * factorDen)
				i = d.end
				continue
			}

			result += ch
			i++
		}
		return result
	}

	const normalizeAbc = (source) => {
		const normalized = (source || "").replace(/\r\n/g, "\n")
		const lines = normalized.split("\n")

		let globalL = { num: 1, den: 8 }
		const headerLines = []
		const bodyLines = []
		let inBody = false

		for (const line of lines) {
			const trimmed = line.trim()
			if (!inBody) {
				const lm = trimmed.match(/^L:\s*(\d+)\s*\/\s*(\d+)/)
				if (lm) globalL = { num: parseInt(lm[1], 10), den: parseInt(lm[2], 10) }
				headerLines.push(line)
				if (/^K:/.test(trimmed)) inBody = true
				continue
			}
			bodyLines.push(line)
		}

		const voiceLs = new Map()
		const info = []
		let currentVoice = null
		let currentL = { ...globalL }
		const usedDens = new Set([globalL.den])

		for (const line of bodyLines) {
			const trimmed = line.trim()

			const vm = trimmed.match(/^V:\s*(\S+)/)
			if (vm) {
				currentVoice = vm[1]
				currentL = voiceLs.has(currentVoice) ? { ...voiceLs.get(currentVoice) } : { ...globalL }
				info.push({ line, kind: "vfield" })
				continue
			}

			const lm = trimmed.match(/^L:\s*(\d+)\s*\/\s*(\d+)/)
			if (lm) {
				currentL = { num: parseInt(lm[1], 10), den: parseInt(lm[2], 10) }
				usedDens.add(currentL.den)
				if (currentVoice) voiceLs.set(currentVoice, { ...currentL })
				else globalL = { ...currentL }
				info.push({ line, kind: "lfield" })
				continue
			}

			if (!trimmed || trimmed.startsWith("%") || fieldRe.test(trimmed)) {
				info.push({ line, kind: "field" })
				continue
			}

			info.push({ line, kind: "music", L: { ...currentL } })
		}

		if (usedDens.size <= 1) return source

		const unifiedDen = Math.max(...Array.from(usedDens))
		const unifiedL = { num: 1, den: unifiedDen }

		const outHeader = []
		let hasL = false
		for (const line of headerLines) {
			const trimmed = line.trim()
			if (/^L:/.test(trimmed)) {
				outHeader.push(`L:${unifiedL.num}/${unifiedL.den}`)
				hasL = true
				continue
			}
			if (/^K:/.test(trimmed) && !hasL) {
				outHeader.push(`L:${unifiedL.num}/${unifiedL.den}`)
				hasL = true
			}
			outHeader.push(line)
		}

		const outBody = []
		for (const it of info) {
			if (it.kind === "lfield") continue
			if (it.kind === "music") {
				const fnum = unifiedL.den * it.L.num
				const fden = it.L.den * unifiedL.num
				outBody.push(rewriteDurations(it.line, fnum, fden))
				continue
			}
			outBody.push(it.line)
		}

		return outHeader.join("\n") + "\n" + outBody.join("\n") + "\n"
	}

	const toStandardAbc = (source) => {
		const abc = hasAbcxBody(source) ? analyze(source).abc : source
		return normalizeAbc(abc)
	}

	const toStandardAbcx = (source) => {
		if (hasAbcxBody(source)) return normalizeAbc(source)
		return abcToAbcx(normalizeAbc(source))
	}

	const hasAbcxBody = (source) => {
		const lines = (source || "").replace(/\r\n/g, "\n").split("\n")
		let inBody = false
		for (const line of lines) {
			const trimmed = line.trim()
			if (!inBody) {
				if (/^K:/.test(trimmed)) inBody = true
				continue
			}
			if (!trimmed || trimmed.startsWith("%") || fieldRe.test(trimmed)) continue
			if (splitTopLevel(stripComment(trimmed), ";").length > 1) return true
		}
		return false
	}

	const abcToAbcx = (source) => {
		if (hasAbcxBody(source)) return source

		const normalized = (source || "").replace(/\r\n/g, "\n")
		const allLines = normalized.split("\n")
		const headerLines = []
		const middleLines = []
		const rawBody = []
		let phase = "header"

		for (const line of allLines) {
			const trimmed = line.trim()
			if (phase === "header") {
				headerLines.push(line)
				if (/^K:/.test(trimmed)) phase = "middle"
				continue
			}
			if (phase === "middle") {
				const isField = fieldRe.test(trimmed)
				const isDirective = trimmed.startsWith("%")
				if (!trimmed || isField || isDirective) {
					middleLines.push(line)
					continue
				}
				phase = "body"
			}
			rawBody.push(line)
		}

		const merged = []
		let buffer = ""
		for (const line of rawBody) {
			if (/\\\s*$/.test(line)) {
				buffer += line.replace(/\\\s*$/, " ")
			} else {
				merged.push(buffer + line)
				buffer = ""
			}
		}
		if (buffer) merged.push(buffer)

		const voiceOrder = []
		const voiceBars = new Map()
		const ensureVoice = (raw) => {
			const norm = normalizeVoiceName(raw)
			if (!voiceBars.has(norm)) {
				voiceOrder.push(norm)
				voiceBars.set(norm, [])
			}
			return norm
		}

		for (const line of middleLines) {
			const v = line.trim().match(/^V:\s*(\S+)/)
			if (v) ensureVoice(v[1])
		}

		let currentVoice = voiceOrder[0] || ensureVoice("1")

		for (const line of merged) {
			const trimmed = line.trim()
			if (!trimmed || trimmed.startsWith("%")) continue

			const vField = trimmed.match(/^V:\s*(\S+)/)
			if (vField) {
				currentVoice = ensureVoice(vField[1])
				continue
			}

			const inlineV = trimmed.match(/^\[V:([^\]]+)\]\s*(.*)$/)
			let voiceName, content
			if (inlineV) {
				voiceName = ensureVoice(inlineV[1])
				content = inlineV[2]
			} else {
				voiceName = currentVoice
				content = trimmed
			}

			const cleaned = stripComment(content).trim()
			if (!cleaned) continue
			const measures = splitAbcMeasures(cleaned)
			for (const m of measures) voiceBars.get(voiceName).push(m)
		}

		const outHeader = headerLines.slice()
		const hasScore = outHeader.some((l) => l.trim().startsWith("%%score"))
		if (!hasScore) {
			let kIdx = -1
			for (let i = outHeader.length - 1; i >= 0; i--) {
				if (/^K:/.test(outHeader[i].trim())) { kIdx = i; break }
			}
			const scoreLine = `%%score ${voiceOrder.map((v) => `(${v})`).join(" ")}`
			if (kIdx >= 0) outHeader.splice(kIdx, 0, scoreLine)
			else outHeader.push(scoreLine)
		}

		const maxBars = Math.max(...voiceOrder.map((v) => voiceBars.get(v).length), 0)
		const outBody = []
		const sep = voiceOrder.length > 1 ? " ; " : ""

		for (let i = 0; i < maxBars; i++) {
			const parts = voiceOrder.map((v) => {
				const m = voiceBars.get(v)[i]
				return m ? m.content.trim() : "z"
			})
			let prefix = ""
			let suffix = ""
			for (const v of voiceOrder) {
				const m = voiceBars.get(v)[i]
				if (m) {
					if (!prefix && m.prefix) prefix = m.prefix
					if (!suffix && m.suffix) suffix = m.suffix
				}
			}
			let row = ""
			if (prefix) row += prefix + " "
			row += parts.join(sep)
			if (suffix) row += " " + suffix
			outBody.push(row.trim())
		}

		const middleStr = middleLines.length ? middleLines.join("\n") + "\n" : ""
		return outHeader.join("\n") + "\n" + middleStr + outBody.join("\n") + "\n"
	}

	const splitAbcMeasures = (text) => {
		const result = []
		let prefix = ""
		let content = ""
		let i = 0
		let quote = false
		let bracket = 0

		const isBarStart = () => {
			if (quote || bracket > 0) return false
			const ch = text[i]
			const next = text[i + 1] || ""
			if (ch === "|") return true
			if (ch === ":" && next === "|") return true
			if (ch === "[" && next === "|") return true
			return false
		}

		const consumeBarDelim = () => {
			let delim = ""
			if (text[i] === ":" || text[i] === "[") delim += text[i++]
			if (text[i] === "|") delim += text[i++]
			while (i < text.length && /[:|\]\[]/.test(text[i])) delim += text[i++]
			if (i < text.length && /\d/.test(text[i])) delim += text[i++]
			return delim
		}

		while (i < text.length) {
			const ch = text[i]
			if (ch === "\"") { quote = !quote; content += ch; i++; continue }
			if (!quote) {
				if (ch === "[" && text[i + 1] !== "|") {
					bracket++
					content += ch; i++; continue
				}
				if (ch === "]" && bracket > 0) {
					bracket--
					content += ch; i++; continue
				}
			}
			if (isBarStart()) {
				const delim = consumeBarDelim()
				if (!content.trim() && !prefix) {
					prefix = delim
				} else {
					result.push({ prefix, content: content.trim(), suffix: delim })
					prefix = ""
					content = ""
				}
			} else {
				content += text[i++]
			}
		}
		if (content.trim()) result.push({ prefix, content: content.trim(), suffix: "" })
		return result
	}

	return {
		analyze,
		isAbcx,
		hasAbcxBody,
		convert: (source) => analyze(source).abc,
		abcxToAbc,
		abcToAbcx,
		normalizeAbc,
		toStandardAbc,
		toStandardAbcx,
		_measureDuration: measureDuration
	}
})
