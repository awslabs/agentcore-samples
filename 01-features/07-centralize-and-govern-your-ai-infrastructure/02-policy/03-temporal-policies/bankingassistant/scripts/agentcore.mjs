#!/usr/bin/env node
/** Run the pinned CLI with project-private preferences, never global settings. */
import { chmodSync, lstatSync, mkdirSync, writeFileSync, renameSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const originalHome = homedir();
const home = join(root, '.sample-cli-home');
const config = join(home, '.agentcore');
for (const directory of [home, config]) {
  try {
    if (lstatSync(directory).isSymbolicLink()) {
      throw new Error('Private CLI directories must not redirect to another settings store');
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  chmodSync(directory, 0o700);
}
// These are CLI preferences, not deployment state, tokens, or AWS credentials.
// This sample owns its tested CDK locks. Do not change account-wide transaction
// search or the user's global dependency-management/telemetry preferences.
const temporary = join(config, `config.${process.pid}.tmp`);
writeFileSync(
  temporary,
  JSON.stringify({
    disableDependencyManagement: true,
    transactionSearch: false,
    telemetry: { enabled: false },
  }) + '\n',
  { flag: 'wx', mode: 0o600 }
);
renameSync(temporary, join(config, 'config.json'));

const result = spawnSync(
  process.execPath,
  [join(root, 'node_modules/@aws/agentcore/dist/main.js'), ...process.argv.slice(2)],
  {
    cwd: root,
    stdio: 'inherit',
    env: {
      ...process.env,
      HOME: home,
      AGENTCORE_CONFIG_DIR: config,
      AGENTCORE_DISABLE_KEYCHAIN: '1',
      AWS_CONFIG_FILE: process.env.AWS_CONFIG_FILE ?? join(originalHome, '.aws/config'),
      AWS_SHARED_CREDENTIALS_FILE: process.env.AWS_SHARED_CREDENTIALS_FILE ?? join(originalHome, '.aws/credentials'),
    },
  }
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
