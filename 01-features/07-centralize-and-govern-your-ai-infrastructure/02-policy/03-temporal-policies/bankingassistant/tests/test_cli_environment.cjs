// Test the real launcher with an inert CLI fixture, not AWS or a replacement launcher.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function fixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'banking-cli-env-'));
  const root = path.join(directory, 'sample');
  const home = path.join(directory, 'operator-home');
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.mkdirSync(path.join(root, 'node_modules/@aws/agentcore/dist'), { recursive: true });
  fs.mkdirSync(path.join(home, '.agentcore'), { recursive: true });
  fs.writeFileSync(path.join(home, '.agentcore/config.json'), 'global settings must remain unchanged');
  fs.copyFileSync(path.resolve(__dirname, '../scripts/agentcore.mjs'), path.join(root, 'scripts/agentcore.mjs'));
  fs.writeFileSync(
    path.join(root, 'node_modules/@aws/agentcore/dist/main.js'),
    `
    console.log(JSON.stringify({
      args: process.argv.slice(2), cwd: process.cwd(), home: process.env.HOME,
      config: process.env.AWS_CONFIG_FILE, credentials: process.env.AWS_SHARED_CREDENTIALS_FILE,
      profile: process.env.AWS_PROFILE, keychain: process.env.AGENTCORE_DISABLE_KEYCHAIN
    }));
  `
  );
  const run = (extra = {}) =>
    spawnSync(process.execPath, [path.join(root, 'scripts/agentcore.mjs'), '--version'], {
      cwd: directory,
      encoding: 'utf8',
      env: { PATH: process.env.PATH, HOME: home, AWS_PROFILE: 'test-profile', ...extra },
    });
  return { directory, root, home, run };
}

test('launcher confines preferences and preserves AWS file references', () => {
  const f = fixture();
  try {
    for (const explicit of [false, true]) {
      const extra = explicit
        ? { AWS_CONFIG_FILE: '/fake/config', AWS_SHARED_CREDENTIALS_FILE: '/fake/credentials' }
        : {};
      const result = f.run(extra);
      assert.equal(result.status, 0, result.stderr);
      const observed = JSON.parse(result.stdout);
      assert.deepEqual(observed.args, ['--version']);
      assert.equal(observed.cwd, f.root);
      assert.equal(observed.home, path.join(f.root, '.sample-cli-home'));
      assert.equal(observed.config, explicit ? '/fake/config' : path.join(f.home, '.aws/config'));
      assert.equal(observed.credentials, explicit ? '/fake/credentials' : path.join(f.home, '.aws/credentials'));
      assert.equal(observed.profile, 'test-profile');
      assert.equal(observed.keychain, '1');
      const preferences = JSON.parse(fs.readFileSync(path.join(observed.home, '.agentcore/config.json')));
      assert.deepEqual(preferences, {
        disableDependencyManagement: true,
        transactionSearch: false,
        telemetry: { enabled: false },
      });
      assert.equal(fs.statSync(observed.home).mode & 0o777, 0o700);
      assert.equal(fs.statSync(path.join(observed.home, '.agentcore')).mode & 0o777, 0o700);
      assert.equal(fs.existsSync(path.join(observed.home, '.aws')), false, 'no auth cache or credentials copied');
      assert.equal(
        fs.readFileSync(path.join(f.home, '.agentcore/config.json'), 'utf8'),
        'global settings must remain unchanged'
      );
    }
  } finally {
    fs.rmSync(f.directory, { recursive: true, force: true });
  }
});

test('launcher refuses a private-HOME symlink before executing the CLI', () => {
  const f = fixture();
  try {
    fs.symlinkSync(f.home, path.join(f.root, '.sample-cli-home'), 'dir');
    const result = f.run();
    assert.notEqual(result.status, 0);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /must not redirect/);
    assert.equal(
      fs.readFileSync(path.join(f.home, '.agentcore/config.json'), 'utf8'),
      'global settings must remain unchanged'
    );
  } finally {
    fs.rmSync(f.directory, { recursive: true, force: true });
  }
});
