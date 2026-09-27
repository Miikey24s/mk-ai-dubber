import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const sourcePath = fileURLToPath(new URL('../src/index.css', import.meta.url));
const sharedSnapshotPath = fileURLToPath(new URL('../src/ui-system.snapshot.css', import.meta.url));
const [sourceCss, sharedSnapshotCss] = await Promise.all([
  readFile(sourcePath, 'utf8'),
  readFile(sharedSnapshotPath, 'utf8'),
]);

assert.match(sourceCss, /@media\s*\(prefers-reduced-motion:\s*reduce\)/, 'VI CSS must honor reduced-motion preferences');
assert.match(sourceCss, /animation-duration:\s*0\.001ms\s*!important/, 'reduced motion must stop long-running animations');
assert.match(sourceCss, /transition-duration:\s*0\.001ms\s*!important/, 'reduced motion must shorten transitions');
assert.match(sharedSnapshotCss, /\.ui-button:focus-visible\s*\{[^}]*outline:/s, 'shared button contract must retain visible focus');

console.log(JSON.stringify({ status: 'PASS', checks: 4 }));
