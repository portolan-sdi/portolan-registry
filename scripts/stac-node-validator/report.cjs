#!/usr/bin/env node
// Validate every STAC JSON file under a directory with stac-node-validator
// and print the findings as one JSON document on stdout.
//
//     node scripts/stac-node-validator/report.cjs MIRROR_DIR
//
// The validator CLI prints a human report and has no JSON mode, so the gate
// calls the library API instead. The exit code is 0 when the run completed,
// whatever it found. The Python caller reads `findings` to decide. A nonzero
// exit means the run itself failed.

'use strict';

const path = require('path');
const validate = require('stac-node-validator');
const nodeLoader = require('stac-node-validator/src/loader/node');
const { resolveFiles } = require('stac-node-validator/src/nodeUtils');

// The CLI drops these when a file has more than one error. They restate the
// errors of each failed branch, so they add length and no information.
const SCHEMA_CHOICE = ['anyOf', 'oneOf'];

function message(error) {
  let text = error.message || String(error);
  if (error.params && typeof error.params === 'object') {
    const params = Object.entries(error.params)
      .map(([key, value]) => `${key}: ${value}`)
      .join(', ');
    if (params) {
      text += ` (${params})`;
    }
  }
  return error.instancePath ? `${error.instancePath} ${text}` : text;
}

function findingsOf(report, root) {
  const file = path.relative(root, report.source || report.id || '');
  const findings = [];
  if (report.skipped) {
    findings.push({
      path: file,
      schema: 'skipped',
      message: (report.messages || []).join('; ') || 'not validated',
    });
    return findings;
  }
  const groups = [['core', report.results.core]];
  for (const [uri, errors] of Object.entries(report.results.extensions)) {
    groups.push([uri, errors]);
  }
  for (const [schema, errors] of groups) {
    const kept = errors.length > 1 ? errors.filter((e) => !SCHEMA_CHOICE.includes(e.keyword)) : errors;
    for (const error of kept.length > 0 ? kept : errors) {
      findings.push({ path: file, schema, message: message(error) });
    }
  }
  return findings;
}

async function main() {
  const root = process.argv[2];
  if (!root) {
    console.error('usage: report.cjs MIRROR_DIR');
    process.exit(2);
  }
  const resolved = await resolveFiles([root], -1);
  const failed = Object.entries(resolved.error);
  if (failed.length > 0) {
    throw new Error(failed.map(([file, error]) => `${file}: ${error}`).join('; '));
  }
  const files = resolved.files.sort();
  const findings = [];
  if (files.length > 0) {
    const result = await validate(files, {
      loader: nodeLoader,
      schemaVersions: {
        '2019-09': require('ajv/dist/2019'),
        '2020-12': require('ajv/dist/2020'),
      },
    });
    const reports = result.children.length > 0 ? result.children : [result];
    for (const report of reports) {
      findings.push(...findingsOf(report, root));
    }
  }
  process.stdout.write(JSON.stringify({ files_checked: files.length, findings }) + '\n');
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
