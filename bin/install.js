#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const source = path.resolve(__dirname, '..', 'run-agent-council');
const codexHome = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
const destination = path.join(codexHome, 'skills', 'run-agent-council');

if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log('Install the run-agent-council skill into your Codex skills directory.');
  console.log('Usage: run-agent-council');
  console.log('Override the destination with CODEX_HOME=/path/to/.codex.');
  process.exit(0);
}

if (!fs.existsSync(source)) {
  console.error('The bundled run-agent-council skill directory is missing.');
  process.exit(1);
}

if (fs.existsSync(destination)) {
  console.error(`Already exists: ${destination}`);
  console.error('Remove it first if you want to reinstall the skill.');
  process.exit(1);
}

fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.cpSync(source, destination, { recursive: true });
console.log(`Installed run-agent-council to ${destination}`);
console.log('Open a new Codex task so the skill list is refreshed.');
