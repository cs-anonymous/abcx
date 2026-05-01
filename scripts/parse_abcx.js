#!/usr/bin/env node
// parse_abcx.js — Node bridge: reads ABCX source, calls abcx.analyze(), prints JSON result
// Usage: node scripts/parse_abcx.js <file>   OR   cat file | node scripts/parse_abcx.js
const fs = require("fs")
const path = require("path")
const abcx = require("../src/abcx")

const source = process.argv[2]
	? fs.readFileSync(process.argv[2], "utf-8")
	: fs.readFileSync(0, "utf-8")

const result = abcx.analyze(source)

const errors = result.diagnostics.filter(d => d.severity === "error")
const warnings = result.diagnostics.filter(d => d.severity === "warning")

const out = {
	errors: errors.map(e => ({ line: e.line, column: e.column, message: e.message })),
	warnings: warnings.map(w => ({ line: w.line, column: w.column, message: w.message })),
	isAbcx: result.isAbcx,
	voices: result.voices || [],
	meter: result.meter || null,
	defaultLength: result.defaultLength || null
}

// Optionally include the converted ABC for inspection
if (process.env.SHOW_ABC === "1") {
	out.abc = result.abc
}

process.stdout.write(JSON.stringify(out, null, 2) + "\n")

process.exit(errors.length > 0 ? 1 : 0)
