#!/usr/bin/env node
/**
 * Assert which engine the CLI drives, and where it stages, for every combination that decides it.
 *
 * This is the seam between the two halves of the tool: the CLI resolves a store and a configuration
 * source, then hands the engine flags (`--staging-bucket`, `--local-dir`, `--config-prefix`) that a
 * user never types. The Python suite cannot see that mapping, and the failure it protects against is
 * silent -- a run that stages in S3 when the user asked for local, or the reverse, still succeeds and
 * still writes a report. It only looks wrong later, in the wrong place.
 *
 * How it works: a stub interpreter records the argv the CLI would have run, so nothing is executed
 * and no AWS call is made. Only single-spawn commands are exercised (`check`, `report`), because a
 * full `run` chains four calls and the assertions here are about the flags, not the sequence.
 *
 *   node tools/verify_cli_modes.js
 */
'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const CLI = path.join(ROOT, 'dist', 'cli', 'index.js');

if (!fs.existsSync(CLI)) {
  console.error(`error: ${CLI} is missing. Run \`npm run build\` first.`);
  process.exit(1);
}

const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'cli-modes-'));
const logPath = path.join(workspace, 'argv.log');
const stub = path.join(workspace, 'stub-python');

// The CLI probes the interpreter with `-c 'import sys, boto3; ...'` before using it, so the stub has
// to answer that, then record anything else it is asked to run.
function writeStub(engineInfoJson, bucketInfoJson) {
  fs.writeFileSync(
    stub,
    ['#!/usr/bin/env node', 'const fs = require("node:fs");',
     'const argv = process.argv.slice(2);',
     'if (argv[0] === "-c") { console.log("(3, 12)"); process.exit(0); }',
     `fs.appendFileSync(${JSON.stringify(logPath)}, argv.join(" ") + "\\n");`,
     `if (argv.includes("engine-info")) { console.log(${JSON.stringify(engineInfoJson)}); }`,
     `if (argv.includes("bucket-info")) { console.log(${JSON.stringify(bucketInfoJson)}); }`,
     'process.exit(0);'].join('\n'),
    { mode: 0o755 },
  );
}

const REGISTRIES = [
  {
    id: 'map-a',
    source: { accountId: '111122223333', region: 'us-east-1', registryId: 'src' },
    target: { accountId: '111122223333', region: 'us-east-1', registryId: 'tgt' },
  },
];

function configFile(name, engine) {
  const file = path.join(workspace, name);
  fs.writeFileSync(file, JSON.stringify({ engine, registries: REGISTRIES }, null, 2));
  return file;
}

const deployed = configFile('deployed.json', {
  account: '111122223333',
  region: 'us-east-1',
  stagingBucket: 'a-deployed-bucket',
  parameterPrefix: '/agent-registry-migration/default',
});
const undeployed = configFile('undeployed.json', {
  account: '111122223333',
  region: 'us-east-1',
});

function run(args, engineInfoJson = '', bucketInfoJson = '') {
  writeStub(engineInfoJson, bucketInfoJson);
  fs.writeFileSync(logPath, '');
  const result = spawnSync(process.execPath, [CLI, ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    env: { ...process.env, PYTHON: stub },
  });
  const recorded = fs.existsSync(logPath)
    ? fs.readFileSync(logPath, 'utf8').split('\n').filter(Boolean)
    : [];
  return { status: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '', recorded };
}

// Answering `engine-info` lets the Glue code path be exercised with no deployment, which is the
// only way to assert what the jobs are actually started with.
const ENGINE_INFO = JSON.stringify({
  stackName: 'AgentRegistryMigrationEngine',
  status: 'UPDATE_COMPLETE',
  outputs: {
    ExtractJobName: 'ExtractJob-STUB',
    TransformLoadJobName: 'TransformLoadJob-STUB',
    StagingBucketName: 'a-deployed-bucket',
  },
});

const cases = [
  {
    name: 'a deployed bucket is used by default',
    args: ['check', '--config', deployed, '--offline'],
    expect: (call) => call.includes('--staging-bucket a-deployed-bucket') && !call.includes('--local-dir'),
  },
  {
    name: '--local overrides a deployed bucket',
    args: ['check', '--config', deployed, '--offline', '--local'],
    expect: (call) => call.includes('--local-dir') && !call.includes('--staging-bucket'),
  },
  {
    name: 'no deployment means local, with no flag needed',
    args: ['check', '--config', undeployed, '--offline'],
    expect: (call) => call.includes('--local-dir') && !call.includes('--staging-bucket'),
  },
  {
    name: '--glue reads the deployed parameters, not the file',
    args: ['check', '--config', deployed, '--glue'],
    expect: (call) =>
      call.includes('--config-prefix /agent-registry-migration/default') &&
      !call.includes('--config-file') &&
      !call.includes('--local-dir'),
  },
  {
    name: 'report honours --local too',
    args: ['report', '--config', deployed, '--local'],
    expect: (call) => call.startsWith('-m migration_common report') && call.includes('--local-dir'),
  },
  {
    name: 'report uses the deployed bucket by default',
    args: ['report', '--config', deployed],
    expect: (call) => call.includes('--staging-bucket a-deployed-bucket'),
  },
  {
    // check reports the run you are about to make. Without --live forwarded it answered about a dry
    // run -- "will NOT write to any target registry" -- for someone who asked about a live one.
    name: 'check --live forwards the write decision to the pre-flight',
    args: ['check', '--config', deployed, '--offline', '--live'],
    expect: (call) => call.includes('--live true'),
  },
  {
    name: 'check without --live stays a dry-run pre-flight',
    args: ['check', '--config', deployed, '--offline'],
    expect: (call) => !call.includes('--live'),
  },
];

let failures = 0;

for (const testCase of cases) {
  const { recorded } = run(testCase.args);
  const call = recorded[0] ?? '';
  const ok = Boolean(call) && testCase.expect(call);
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${testCase.name}`);
  if (!ok) {
    failures += 1;
    console.log(`       args    : ${testCase.args.join(' ')}`);
    console.log(`       recorded: ${call || '(the engine was never invoked)'}`);
  }
}

// The local run chain: four engine calls, in order, each carrying the scope and the staging choice.
// The orchestration moved from Python (where a test covered it) into TypeScript (where none did),
// which is how the Glue scope bug below shipped green.
const localChain = run(
  ['run', '--config', undeployed, '--incremental', '--since', '2026-08-01T00:00:00Z', '--live'],
  ENGINE_INFO,
);
const commands = localChain.recorded.map((call) => call.split(' ')[2]);
const chainOk =
  commands.join(',') === 'check,extract,load,report' &&
  localChain.recorded.every(
    (call) => call.includes('--local-dir') && call.includes('--load-mode INCREMENTAL'),
  ) &&
  // --live must be explicit on the load, and must never appear on the read-only stage.
  localChain.recorded.some((call) => call.includes(' load ') && call.includes('--live true')) &&
  !localChain.recorded.some((call) => call.includes(' extract ') && call.includes('--live'));
console.log(`  ${chainOk ? 'ok  ' : 'FAIL'} the local run chain is check -> extract -> load -> report, all scoped`);
if (!chainOk) {
  failures += 1;
  for (const call of localChain.recorded) {
    console.log(`       ${call}`);
  }
}

// A per-run decision has to reach the Glue JOB, not just the local stages. This one shipped green
// once: the header said "records changed since ..." while the job read the deployed loadMode and
// re-read every record. Nothing in the offline suite can see the arguments a job is started with.
const glueScope = run(
  ['run', '--config', deployed, '--glue', '--incremental', '--since', '2026-08-01T00:00:00Z'],
  ENGINE_INFO,
);
const jobStarts = glueScope.recorded.filter((call) => call.includes('glue-run'));
const scopedJobs =
  jobStarts.length === 2 &&
  jobStarts.every(
    (call) =>
      call.includes('--load-mode INCREMENTAL') && call.includes('--changed-after 2026-08-01T00:00:00Z'),
  );
console.log(`  ${scopedJobs ? 'ok  ' : 'FAIL'} --incremental/--since reach both Glue job starts`);
if (!scopedJobs) {
  failures += 1;
  for (const call of jobStarts) {
    console.log(`       ${call}`);
  }
  if (!jobStarts.length) {
    console.log('       no glue-run call was recorded');
  }
}

// --live has to reach the job too, and must be explicit on every load.
const liveOnGlue = run(['run', '--config', deployed, '--glue', '--live', '--resume', 'RID'], ENGINE_INFO);
const loadCall = liveOnGlue.recorded.find((call) => call.includes('glue-run') && call.includes('--live'));
const liveForwarded = Boolean(loadCall && loadCall.includes('--live true'));
console.log(`  ${liveForwarded ? 'ok  ' : 'FAIL'} --live reaches the Glue load job`);
if (!liveForwarded) {
  failures += 1;
  console.log(`       recorded: ${loadCall ?? '(no load job start)'}`);
}

// Naming both places must be refused rather than resolved to one of them.
const both = run(['run', '--config', deployed, '--glue', '--local']);
const refused = both.status !== 0 && /Pass one/.test(both.stdout + both.stderr) && both.recorded.length === 0;
console.log(`  ${refused ? 'ok  ' : 'FAIL'} --glue with --local is refused, and nothing runs`);
if (!refused) {
  failures += 1;
  console.log(`       status: ${both.status}, recorded: ${both.recorded.length} call(s)`);
}

// The two-step flow: `extract` reads and cannot write, `load` writes only when told to, and both
// drive the same stages `run` does.
const extractOnly = run(['extract', '--config', undeployed], ENGINE_INFO);
const extractCommands = extractOnly.recorded.map((call) => call.split(' ')[2]);
const extractOk =
  extractCommands.join(',') === 'check,extract,report' &&
  // No --live reaches any stage of an extract: the command has no such flag, and the stage that
  // could write is not run at all.
  !extractOnly.recorded.some((call) => call.includes('--live')) &&
  extractOnly.recorded.every((call) => call.includes('--local-dir'));
console.log(`  ${extractOk ? 'ok  ' : 'FAIL'} extract runs check -> extract -> report, and never --live`);
if (!extractOk) {
  failures += 1;
  for (const call of extractOnly.recorded) {
    console.log(`       ${call}`);
  }
}

const loadNamed = run(['load', '--config', undeployed, '--live', '--run-id', 'RID-42'], ENGINE_INFO);
const loadCommands = loadNamed.recorded.map((call) => call.split(' ')[2]);
const loadOk =
  loadCommands.join(',') === 'check,load,report' &&
  // The named run id is what gets loaded, and the write decision is explicit on the load.
  loadNamed.recorded.every((call) => !call.includes('--run-id') || call.includes('--run-id RID-42')) &&
  loadNamed.recorded.some((call) => call.includes(' load ') && call.includes('--live true')) &&
  // Nothing is re-read: an extract stage in a load would defeat the point of reviewing one.
  !loadCommands.includes('extract');
console.log(`  ${loadOk ? 'ok  ' : 'FAIL'} load runs check -> load -> report for the run id it was given`);
if (!loadOk) {
  failures += 1;
  for (const call of loadNamed.recorded) {
    console.log(`       ${call}`);
  }
}

// With no run id, `load` asks the engine which extract is most recent rather than guessing, and says
// what to do when there is none.
const loadLatest = run(['load', '--config', undeployed, '--live'], ENGINE_INFO);
const askedForLatest = loadLatest.recorded.some((call) => call.includes('latest-run'));
const guided =
  loadLatest.status !== 0 &&
  /extract/.test(loadLatest.stdout + loadLatest.stderr) &&
  !loadLatest.recorded.some((call) => call.includes(' load '));
console.log(
  `  ${askedForLatest && guided ? 'ok  ' : 'FAIL'} load with no run id resolves the latest extract, and says so when there is none`,
);
if (!(askedForLatest && guided)) {
  failures += 1;
  console.log(`       status: ${loadLatest.status}, recorded: ${loadLatest.recorded.join(' | ')}`);
}

// A flag the command does not act on has to be refused, not dropped. Checking flags against one
// global list accepted every one of these and then ignored it, which reads as agreement.
const misplaced = [
  ['check', '--resume', 'RID'],
  ['check', '--yes'],
  ['report', '--live'],
  ['init', '--json'],
  ['init', '--incremental'],
  // Extraction cannot write, so the flag that writes must not be accepted on it.
  ['extract', '--live'],
  // The records a load covers were chosen when they were extracted.
  ['load', '--incremental'],
];
let misplacedOk = true;
for (const args of misplaced) {
  const attempt = run([...args, '--config', deployed]);
  const output = attempt.stdout + attempt.stderr;
  const ok =
    attempt.status === 2 &&
    new RegExp(`does not take ${args.find((a) => a.startsWith('--'))}`).test(output) &&
    attempt.recorded.length === 0;
  if (!ok) {
    misplacedOk = false;
    console.log(`       ${args.join(' ')} -> status ${attempt.status}, ${attempt.recorded.length} call(s)`);
  }
}
console.log(`  ${misplacedOk ? 'ok  ' : 'FAIL'} a flag aimed at the wrong command is refused, and nothing runs`);
if (!misplacedOk) {
  failures += 1;
}

// The two things people type first have to answer about the command they asked about.
const helpCases = [
  { args: ['run', '--help'], needle: 'agent-registry-migration run --' },
  { args: ['help', 'destroy'], needle: 'agent-registry-migration destroy --' },
  { args: ['help'], needle: 'Usage: agent-registry-migration <command>' },
];
let helpOk = true;
for (const { args, needle } of helpCases) {
  const attempt = run(args);
  const ok = attempt.status === 0 && attempt.stdout.includes(needle) && attempt.recorded.length === 0;
  if (!ok) {
    helpOk = false;
    console.log(`       ${args.join(' ')} -> status ${attempt.status}: ${attempt.stdout.split('\n')[0]}`);
  }
}
// And no command at all is a usage error, matching the Python entrypoint rather than reporting
// success to a script that passed nothing.
const bare = run([]);
if (bare.status !== 2) {
  helpOk = false;
  console.log(`       (no arguments) -> status ${bare.status}, expected 2`);
}
console.log(`  ${helpOk ? 'ok  ' : 'FAIL'} help works as typed, and no command exits 2`);
if (!helpOk) {
  failures += 1;
}

// deploy names its bucket deterministically (stackName-account-region, see stagingBucketName in
// lib/config.ts) rather than letting CloudFormation generate a random one. When no stack exists
// under this name but a bucket with that exact name already does -- the normal result of `destroy`
// without --delete-data, which keeps the bucket on purpose -- CloudFormation itself would fail the
// whole stack create with BucketAlreadyOwnedByYou. deploy has to catch this and refuse before ever
// invoking CDK, not let that failure surface several minutes into a real deployment.
const EMPTY_ENGINE_INFO = ''; // no stack under this name: engine-info prints nothing, deploy proceeds past that check
const OWNED_BUCKET = JSON.stringify({ exists: true, accessible: true, ownedByCaller: true, applicationTag: 'AgentRegistryMigration' });
const collision = run(['deploy', '--config', undeployed, '--yes'], EMPTY_ENGINE_INFO, OWNED_BUCKET);
const collisionRefused =
  collision.status === 1 &&
  /already exists/.test(collision.stderr) &&
  /destroy.*without --delete-data|delete-data/.test(collision.stderr) &&
  // Refused before CDK ever runs: no "deploy" call was recorded (the stub only sees Python calls,
  // so an empty/short recorded list here means runCdk -- a different binary -- was never reached).
  !collision.recorded.some((call) => call.includes('publish-artifacts'));
console.log(`  ${collisionRefused ? 'ok  ' : 'FAIL'} deploy refuses when its deterministic bucket name already exists`);
if (!collisionRefused) {
  failures += 1;
  console.log(`       status: ${collision.status}`);
  console.log(`       stderr: ${collision.stderr.split('\n').join('\n               ')}`);
}

// A bucket that exists under this account but was never created by this tool (no Application tag)
// must not be silently reused -- still refused, with different guidance naming that it is unrelated.
const UNRELATED_BUCKET = JSON.stringify({ exists: true, accessible: true, ownedByCaller: true, applicationTag: null });
const unrelated = run(['deploy', '--config', undeployed, '--yes'], EMPTY_ENGINE_INFO, UNRELATED_BUCKET);
const unrelatedRefused =
  unrelated.status === 1 && /was not created by this tool/.test(unrelated.stderr);
console.log(`  ${unrelatedRefused ? 'ok  ' : 'FAIL'} deploy refuses (with different guidance) for a bucket it did not create`);
if (!unrelatedRefused) {
  failures += 1;
  console.log(`       status: ${unrelated.status}, stderr: ${unrelated.stderr.split('\n')[0]}`);
}

// No collision: deploy proceeds to CDK. It will fail here (no real cdk binary set up for this
// harness) but the point is it gets PAST the bucket check -- confirmed by the failure being a CDK
// launch error, not the collision message.
const NO_BUCKET = JSON.stringify({ exists: false });
const clear = run(['deploy', '--config', undeployed, '--yes'], EMPTY_ENGINE_INFO, NO_BUCKET);
const proceeded = !/already exists/.test(clear.stderr);
console.log(`  ${proceeded ? 'ok  ' : 'FAIL'} deploy proceeds past the bucket check when there is no collision`);
if (!proceeded) {
  failures += 1;
  console.log(`       stderr: ${clear.stderr.split('\n')[0]}`);
}

// A deploy with no TTY and no --yes must refuse BEFORE the CDK toolkit runs. Left to itself, the
// toolkit cannot ask for the IAM confirmation, and what it leaves behind is a stack shell in
// REVIEW_IN_PROGRESS carrying termination protection -- which cannot delete itself, so every later
// deploy fails on "cannot be deleted while TerminationProtection is enabled". This harness never has
// a TTY, which is exactly the condition being asserted.
const noTty = run(['deploy', '--config', undeployed], EMPTY_ENGINE_INFO, NO_BUCKET);
const noTtyRefused =
  noTty.status === 1 &&
  /--yes/.test(noTty.stderr) &&
  /REVIEW_IN_PROGRESS/.test(noTty.stderr) &&
  // Refused before anything ran: no bucket check, no CDK.
  !/already exists/.test(noTty.stderr);
console.log(`  ${noTtyRefused ? 'ok  ' : 'FAIL'} deploy without --yes refuses when there is no TTY to confirm at`);
if (!noTtyRefused) {
  failures += 1;
  console.log(`       status: ${noTty.status}`);
  console.log(`       stderr: ${noTty.stderr.split('\n').join('\n               ')}`);
}

// A stack already stuck in REVIEW_IN_PROGRESS is cleared rather than reported as an existing
// installation to join -- it holds no resources and cannot be updated, so joining it is impossible.
const REVIEW_STACK = JSON.stringify({
  stackName: 'AgentRegistryMigrationEngine',
  status: 'REVIEW_IN_PROGRESS',
  outputs: {},
});
const wedged = run(['deploy', '--config', undeployed, '--yes'], REVIEW_STACK, NO_BUCKET);
const wedgeCleared =
  wedged.recorded.some((call) => call.includes('clear-pending-stack')) &&
  /REVIEW_IN_PROGRESS/.test(wedged.stdout) &&
  // Must NOT claim there is an installation to join.
  !/Joining an existing installation/.test(wedged.stdout);
console.log(`  ${wedgeCleared ? 'ok  ' : 'FAIL'} deploy clears a stack stranded in REVIEW_IN_PROGRESS instead of joining it`);
if (!wedgeCleared) {
  failures += 1;
  console.log(`       recorded: ${wedged.recorded.join(' | ')}`);
  console.log(`       stdout: ${wedged.stdout.split('\n').slice(0, 4).join('\n               ')}`);
}

// The next step printed after an extract has to stay in the same place the extract ran. Suggesting
// a bare `load` after `extract --glue` points at a local load of a run staged in the deployed
// bucket, which is a different place from the one the sentence above it is describing.
const glueNext = run(['extract', '--config', deployed, '--glue'], ENGINE_INFO);
const glueNextCarriesScope =
  /agent-registry-migration load --glue --dry-run/.test(glueNext.stdout) &&
  /agent-registry-migration load --glue --live/.test(glueNext.stdout);
console.log(`  ${glueNextCarriesScope ? 'ok  ' : 'FAIL'} extract --glue suggests load --glue, not a bare load`);
if (!glueNextCarriesScope) {
  failures += 1;
  const suggestions = glueNext.stdout.split('\n').filter((line) => line.includes('load'));
  console.log(`       suggested: ${suggestions.join('\n                  ')}`);
}

// One registry that cannot be created must not cost the others their ids. The engine works per
// mapping and exits non-zero when any of them failed, so the JSON it printed describes the
// successes *and* the failure -- discarding it on the exit code alone leaves registries that exist
// in the account and appear nowhere in the configuration, and the next run creates them again.
// Asserted here because only the CLI decides what to do with a partial result.
const multi = path.join(workspace, 'multi.json');
fs.writeFileSync(
  multi,
  JSON.stringify(
    {
      engine: { account: '111122223333', region: 'us-east-1' },
      registries: ['map-a', 'map-b'].map((id) => ({
        id,
        source: { accountId: '111122223333', region: 'us-east-1', registryId: `src-${id}` },
        target: { accountId: '111122223333', region: 'us-east-1', registryId: '<new-registry-id>' },
      })),
    },
    null,
    2,
  ),
);
fs.writeFileSync(
  stub,
  [
    '#!/usr/bin/env node',
    'const argv = process.argv.slice(2);',
    'if (argv[0] === "-c") { console.log("(3, 12)"); process.exit(0); }',
    'const derived = ["map-a", "map-b"].map((id) => ({ mappingId: id, region: "us-east-1",',
    '  payload: { name: id }, payloadPath: "/tmp/" + id + ".json", warnings: [] }));',
    'if (argv.includes("target-config") && argv.includes("--create")) {',
    '  derived[0].registryId = "CREATED-A"; derived[0].status = "READY";',
    '  derived[1].createError = "AccessDeniedException on CreateRegistry";',
    '  console.log(JSON.stringify(derived));',
    // What the engine does when any mapping failed, and the whole point of the case.
    '  process.exit(1);',
    '}',
    'if (argv.includes("target-config")) { console.log(JSON.stringify(derived)); }',
    'process.exit(0);',
  ].join('\n'),
  { mode: 0o755 },
);
const partial = spawnSync(process.execPath, [CLI, 'target-config', '--config', multi, '--create'], {
  cwd: ROOT,
  encoding: 'utf8',
  env: { ...process.env, PYTHON: stub },
});
const written = JSON.parse(fs.readFileSync(multi, 'utf8'));
const partialKept =
  written.registries[0].target.registryId === 'CREATED-A' &&
  written.registries[1].target.registryId === '<new-registry-id>' &&
  /CREATED-A/.test(partial.stdout ?? '') &&
  /AccessDeniedException/.test(partial.stdout ?? '');
console.log(`  ${partialKept ? 'ok  ' : 'FAIL'} a create that failed for one mapping still records the ids of the others`);
if (!partialKept) {
  failures += 1;
  console.log(`       recorded ids: ${written.registries.map((r) => r.target.registryId).join(', ')}`);
  console.log(`       stdout: ${(partial.stdout ?? '').split('\n').join('\n               ')}`);
}

fs.rmSync(workspace, { recursive: true, force: true });

if (failures) {
  console.error(`${failures} CLI mode assertion(s) failed`);
  process.exit(1);
}
console.log('CLI drives the expected engine and staging for every mode');
