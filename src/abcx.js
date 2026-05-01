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
		} else if (layout.mode === "auto") {
			const beats = meter.numerator || 4
			barsPerLine = Math.max(1, Math.min(6, Math.round(12 / beats)))
		}

		const linebreakChar = layout.mode === "auto" || layout.mode === "fixed" ? "!" : ""
		const body = convertBody(state.bodyLines, voices, {
			diagnostics,
			meter,
			defaultLength,
			linebreakChar,
			layout: { mode: layout.mode, barsPerLine }
		})

		const abc = buildAbc(state, voices, body, linebreakChar)

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

	const buildAbc = (state, voices, body, linebreakChar) => {
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
				header.push(`${voice[1]}V:${normalized}${voice[3]}`)
				continue
			}
			header.push(item.text)
		}

		if (!hasScore && voices.length > 1) {
			header.push(`%%score ${voices.map((voice) => `(${voice})`).join(" ")}`)
		}

		for (const voice of voices) {
			if (!voiceDefinitions.has(voice)) {
				header.push(`V:${voice}`)
			}
		}

		if (keyLine) header.push(keyLine)
		if (linebreakChar) header.push(`I:linebreak <${linebreakChar}>`)

		return `${header.join("\n")}\n${body.join("\n")}`.trimEnd() + "\n"
	}

	const convertBody = (bodyLines, voices, context) => {
		const output = []
		const linebreakChar = context.linebreakChar || ""
		const layout = context.layout || {}
		let barsPerLine = layout.barsPerLine || null
		let barIndex = 0
		let lastBreakOutputIndex = -1

		let currentMeter = context.meter
		let currentDefaultLength = context.defaultLength

		const meterRe = /\[M:\s*([A-Za-z0-9/|]+)\]/g
		const lengthRe = /\[L:\s*(\d+\/\d+)\]/g

		for (const line of bodyLines) {
			const text = line.text
			if (!text.trim()) {
				output.push("")
				continue
			}
			if (/^\s*%/.test(text) || fieldRe.test(text.trim()) || /^\s*\[[A-Za-z]:/.test(text)) {
				output.push(text)
				let m
				meterRe.lastIndex = 0
				while ((m = meterRe.exec(text))) currentMeter = parseFraction(m[1])
				lengthRe.lastIndex = 0
				while ((m = lengthRe.exec(text))) currentDefaultLength = parseFraction(m[1])
				continue
			}

			const measures = splitMeasures(text)
			if (!measures.length) {
				output.push(text)
				continue
			}

			const perVoice = voices.map(() => "")
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
			}

			for (let index = 0; index < voices.length; index++) {
				output.push(`[V:${voices[index]}] ${perVoice[index].trim()}`)
			}

			const breakOutputIndex = output.length - 1
			if (layout.mode === "original") {
				if (linebreakChar && breakOutputIndex > lastBreakOutputIndex) {
					output[breakOutputIndex] += linebreakChar
					lastBreakOutputIndex = breakOutputIndex
				}
			} else if (barsPerLine !== null && linebreakChar) {
				barIndex += measures.length
				if (barIndex >= barsPerLine) {
					barIndex = 0
					if (breakOutputIndex > lastBreakOutputIndex) {
						output[breakOutputIndex] += linebreakChar
						lastBreakOutputIndex = breakOutputIndex
					}
				}
			}
		}
		return output
	}

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

	return {
		analyze,
		isAbcx,
		convert: (source) => analyze(source).abc,
		_measureDuration: measureDuration
	}
})
