#!/usr/bin/env node

const fs = require("fs")
const path = require("path")
const abcx = require("../src/abcx")

const iterateAbciFiles = function* (rootDir) {
	const stack = [rootDir]
	while (stack.length > 0) {
		const current = stack.pop()
		const entries = fs.readdirSync(current, { withFileTypes: true })
		entries.sort((left, right) => left.name.localeCompare(right.name))
		for (let index = entries.length - 1; index >= 0; index--) {
			const entry = entries[index]
			const fullPath = path.join(current, entry.name)
			if (entry.isDirectory()) {
				stack.push(fullPath)
				continue
			}
			if (entry.isFile() && fullPath.toLowerCase().endsWith(".abci")) {
				yield fullPath
			}
		}
	}
}

const ensureDir = (filePath) => {
	fs.mkdirSync(path.dirname(filePath), { recursive: true })
}

const main = () => {
	const inputRoot = path.resolve(process.argv[2] || process.cwd())
	const outputRoot = path.resolve(process.argv[3] || `${inputRoot}_abcx`)
	const failureReportPath = process.argv[4] ? path.resolve(process.argv[4]) : null

	if (!fs.existsSync(inputRoot) || !fs.statSync(inputRoot).isDirectory()) {
		console.error(`Input directory not found: ${inputRoot}`)
		process.exit(2)
	}

	let converted = 0
	let failed = 0
	let warnings = 0
	let total = 0
	const failureReport = failureReportPath ? fs.createWriteStream(failureReportPath, { flags: "w" }) : null

	const closeFailureReport = () => {
		if (failureReport) {
			failureReport.end()
		}
	}

	try {
		for (const sourcePath of iterateAbciFiles(inputRoot)) {
			total++
			const relativePath = path.relative(inputRoot, sourcePath)
			const outputPath = path.join(outputRoot, relativePath).replace(/\.abci$/i, ".abcx")

			try {
				const source = fs.readFileSync(sourcePath, "utf8")
				const convertedText = abcx.toAbciAbcx(source)
				ensureDir(outputPath)
				fs.writeFileSync(outputPath, convertedText.endsWith("\n") ? convertedText : `${convertedText}\n`, "utf8")

				const validation = abcx.analyze(convertedText)
				const errors = (validation.diagnostics || []).filter((item) => item.severity === "error")
				warnings += (validation.diagnostics || []).filter((item) => item.severity === "warning").length
				if (!validation.isAbcx || errors.length > 0) {
					throw new Error(`validation failed: isAbcx=${validation.isAbcx}, errors=${errors.length}`)
				}

				converted++
				if (total % 100 === 0) {
					console.error(`[${total}] ok=${converted} failed=${failed}`)
				}
			} catch (err) {
				failed++
				const message = err && err.message ? err.message : String(err)
				console.error(`[${total}] FAIL ${sourcePath}: ${message}`)
				if (failureReport) {
					failureReport.write(`${sourcePath}\t${message}\n`)
				}
			}
		}
	} finally {
		closeFailureReport()
	}

	if (total === 0) {
		console.error(`No .abci files found under: ${inputRoot}`)
		process.exit(0)
	}

	console.error(`Done: ok=${converted} failed=${failed} warnings=${warnings} total=${total}`)
	process.exit(failed > 0 ? 1 : 0)
}

main()