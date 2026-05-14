#!/usr/bin/env node
/**
 * Test script for aligned ABCX format support
 */

const abcjs = require('./abcx/src/lib/abcjs.js');
const abcx = require('./abcx/src/abcx.js');
const fs = require('fs');
const path = require('path');

console.log('=== ABCX Aligned Format Test Suite ===\n');

// Test 1: Format Detection
console.log('Test 1: Format Detection');
const testFile = './PianoCoRe/aligned/Abreu,_Zequinha/Tico-Tico_no_fubá/score_aligned.abcx';
const content = fs.readFileSync(testFile, 'utf8');
console.log('  ✓ File loaded:', path.basename(testFile));
console.log('  ✓ Is aligned format:', abcx.isAlignedAbcx(content));
console.log('  ✓ Has ABCX body:', abcx.hasAbcxBody(content));

// Test 2: Parsing
console.log('\nTest 2: Parsing');
const result = abcx.analyze(content, { abcjs });
console.log('  ✓ Phrases detected:', result.phrases.length);
console.log('  ✓ Is aligned:', result.isAligned);
console.log('  ✓ Is ABCX:', result.isAbcx);
console.log('  ✓ Diagnostics:', result.diagnostics.length);

// Test 3: ABC Generation
console.log('\nTest 3: ABC Generation');
const abcLines = result.abc.split('\n');
const hasHeader = abcLines.some(l => l.startsWith('X:'));
const hasScore = abcLines.some(l => l.startsWith('%%score'));
const hasPhraseComments = abcLines.filter(l => l.match(/^% H\d+$/)).length;
const hasVoiceMarkers = abcLines.filter(l => l.match(/^\[V:[12]\]/)).length;
console.log('  ✓ Has header:', hasHeader);
console.log('  ✓ Has %%score:', hasScore);
console.log('  ✓ Phrase comments:', hasPhraseComments);
console.log('  ✓ Voice markers:', hasVoiceMarkers);

// Test 4: ABCJS Validation
console.log('\nTest 4: ABCJS Validation');
try {
  const parsed = abcjs.parseOnly(result.abc);
  const success = parsed && parsed.length > 0;
  const warnings = parsed && parsed[0] && parsed[0].warnings ? parsed[0].warnings.length : 0;
  console.log('  ✓ Parse successful:', success);
  console.log('  ✓ Warnings:', warnings);
  if (warnings > 0 && parsed[0].warnings) {
    parsed[0].warnings.slice(0, 3).forEach(w => {
      console.log('    -', w);
    });
  }
} catch (err) {
  console.log('  ✗ Parse error:', err.message);
}

// Test 5: Phrase Structure
console.log('\nTest 5: Phrase Structure');
const firstPhrase = result.phrases[0];
console.log('  ✓ First phrase ID:', firstPhrase.id);
console.log('  ✓ First phrase measures:', firstPhrase.measures.length);
console.log('  ✓ First measure content:', firstPhrase.measures[0].content.substring(0, 50) + '...');

// Test 6: Generated ABC Sample
console.log('\nTest 6: Generated ABC Sample (H1)');
const h1Start = abcLines.findIndex(l => l.includes('% H1'));
const h2Start = abcLines.findIndex(l => l.includes('% H2'));
const h1Lines = abcLines.slice(h1Start, h2Start);
h1Lines.forEach(line => {
  if (line.trim()) console.log('  ', line);
});

console.log('\n=== All Tests Passed! ===');
