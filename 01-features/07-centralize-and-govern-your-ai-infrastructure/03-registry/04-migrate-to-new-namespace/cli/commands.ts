/**
 * The things a user does. Each one is a whole step of the migration, not a piece of one.
 *
 * Every command resolves the same configuration file and the same staging location, so nothing has
 * to be re-supplied between them. Nothing reaches a target registry unless `--live` says so, on either
 * `run` or `load`.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  CliError,
  MigrationFile,
  RegistryMapping,
  ask,
  bucketInfo,
  confirm,
  describeStaging,
  engineInfo,
  isInteractive,
  newRunId,
  packageRoot,
  readConfig,
  resolveConfigPath,
  runCdk,
  runEngine,
  runEngineJson,
  stackName,
  stagingArguments,
  writeConfig,
} from './context';
// Reuses the exact bucket-naming function the stack itself constructs the bucket with (see
// stagingBucketName in lib/config.ts), rather than a second copy that could silently drift from
// it. Both this CLI and the CDK app (via bin/cdk-app.ts) already import from lib/config.ts, so
// this adds no new dependency to the deploy path.
import { stagingBucketName } from '../lib/config';

export interface Options {
  readonly config?: string;
  readonly json: boolean;
  readonly live: boolean;
  /** `--dry-run`: the default said explicitly. Refused alongside `--live`. */
  readonly dryRun: boolean;
  readonly glue: boolean;
  readonly local: boolean;
  readonly resume?: string;
  /** `--resume` with no id: load the most recent extract that is ready. */
  readonly resumeLatest: boolean;
  readonly incremental: boolean;
  readonly since?: string;
  readonly yes: boolean;
  readonly force: boolean;
  readonly offline: boolean;
  readonly deleteData: boolean;
  readonly keepReports: boolean;
  /** `--create`: apply the derived target registry configuration instead of only printing it. */
  readonly create: boolean;
  readonly runId?: string;
}

function parameterPrefix(config: MigrationFile): string {
  const configured = config.engine?.parameterPrefix;
  if (typeof configured === 'string' && configured) {
    return configured;
  }
  const deploymentId = (config.engine?.['deploymentId'] as string | undefined) ?? 'default';
  return `/agent-registry-migration/${deploymentId}`;
}

/**
 * Tell the engine where its configuration and staging live.
 *
 * A local run reads the configuration file directly. A Glue run reads what the deployment
 * published, because that is what the jobs in the cloud will read -- validating the file instead
 * would check something other than what runs.
 */
function engineArgs(
  config: MigrationFile,
  configPath: string,
  glue: boolean,
  local = false,
): string[] {
  if (glue) {
    // The region travels with the prefix: the configuration parameters and the staging bucket only
    // exist in the region the engine was deployed into, which is frequently not the caller's
    // default. Without it, every --glue command reports a deployment in another region as missing.
    return ['--config-prefix', parameterPrefix(config), ...regionArg(config)];
  }
  return ['--config-file', configPath, ...stagingArguments(config, configPath, local)];
}

function mappingCount(config: MigrationFile): number {
  return Array.isArray(config.registries) ? config.registries.length : 0;
}

/**
 * Which records this run covers, as a per-run override.
 *
 * The documented cutover is a full load followed by an incremental catch-up, so the mode belongs on
 * the command for the same reason `--live` does: the catch-up happens under time pressure, and
 * editing a configuration file is the last thing anyone wants to be doing then.
 */
function scopeArgs(options: Options): string[] {
  if (!options.incremental) {
    return [];
  }
  const args = ['--load-mode', 'INCREMENTAL'];
  if (options.since) {
    args.push('--changed-after', options.since);
  }
  return args;
}

function describeScope(options: Options): string {
  if (!options.incremental) {
    return 'every record (full)';
  }
  return options.since
    ? `records changed since ${options.since}`
    : 'records changed since each pair last loaded successfully';
}

// --------------------------------------------------------------------------------------------
// init
// --------------------------------------------------------------------------------------------

const PLACEHOLDER_TARGET = '<new-registry-id>';

/** How many times a prompt re-asks before giving up, so a typo cannot loop forever unattended. */
const MAX_PROMPT_ATTEMPTS = 3;

/**
 * Ask until the answer is valid, rather than discarding every answer given so far.
 *
 * `init` is a wizard: throwing on the first mistyped value meant re-entering every registry pair
 * that had already been answered. Bounded, so a non-interactive stdin that keeps returning the same
 * unusable value still terminates.
 */
async function askValid(
  prompt: string,
  fallback: string | undefined,
  validate: (answer: string) => string | undefined,
): Promise<string> {
  for (let attempt = 1; ; attempt += 1) {
    const answer = await ask(prompt, fallback);
    const problem = validate(answer);
    if (!problem) {
      return answer;
    }
    if (attempt >= MAX_PROMPT_ATTEMPTS) {
      throw new CliError(problem);
    }
    process.stdout.write(`  ${problem} Try again.\n`);
  }
}

/** Ask for a 12-digit account id, rejecting anything else before it reaches the configuration. */
function askAccount(prompt: string, fallback?: string): Promise<string> {
  return askValid(prompt, fallback, (answer) =>
    /^\d{12}$/.test(answer) ? undefined : `"${answer}" is not a 12-digit AWS account id.`,
  );
}

/**
 * Ask for an AWS region, checking the shape the engine's own pre-flight will check.
 *
 * Validated here for the same reason the account id is: `check` rejects a malformed region (see
 * `_REGION_PATTERN` in preflight.py), and finding that out after the whole wizard has been answered
 * is worse than finding out at the prompt.
 */
function askRegion(prompt: string, fallback?: string): Promise<string> {
  return askValid(prompt, fallback, (answer) =>
    /^[a-z]{2}(-[a-z]+)+-\d$/.test(answer)
      ? undefined
      : `"${answer}" is not an AWS region (for example us-east-1).`,
  );
}

/** Ask for a registry id, which must be present and must not look like a placeholder. */
function askRegistryId(prompt: string, { required }: { required: boolean }): Promise<string> {
  return askValid(prompt, undefined, (answer) => {
    if (!answer) {
      return required ? 'A registry id is required.' : undefined;
    }
    return /^[A-Za-z0-9._\-/:]+$/.test(answer)
      ? undefined
      : `"${answer}" has characters a registry id cannot contain.`;
  });
}

/**
 * Say what a registry in another account needs, at the moment the user says it is in another one.
 *
 * Cross-account works, but not silently: the engine has to assume a role in that account. Which of
 * the two ways applies depends on whether this solution may create IAM, so name both here rather
 * than letting `check` fail with an AccessDenied later.
 */
function explainRemoteAccounts(engineAccount: string, registries: RegistryMapping[]): void {
  const remote = [
    ...new Set(
      registries
        .flatMap((mapping) => [mapping.source.accountId, mapping.target.accountId])
        .filter((accountId) => accountId !== engineAccount),
    ),
  ];
  if (remote.length === 0) {
    return;
  }
  process.stdout.write(
    `\nRegistries in another account: ${remote.join(', ')}.\n` +
      'The engine reaches those by assuming a role there, which is one extra step:\n' +
      '  - deploy the generated access stack in each of those accounts:\n' +
      remote.map((accountId) => `      agent-registry-migration deploy   (as ${accountId})\n`).join('') +
      '  - or, if you manage IAM yourself, set source.roleArn / target.roleArn on the mapping.\n' +
      '"agent-registry-migration check" assumes every role and calls the registry behind it, so it\n' +
      'tells you in seconds whether this is set up.\n',
  );
}

/** One entry from `target-config --json`: what the engine derived for a mapping, and created. */
interface DerivedTarget {
  readonly mappingId: string;
  readonly payloadPath?: string;
  readonly command?: string;
  readonly error?: string;
  /** What about the derived payload needs a decision before it is applied. */
  readonly warnings?: readonly string[];
  /** Set by `--create`: the registry the engine created, and the state it settled in. */
  readonly registryId?: string;
  readonly registryArn?: string;
  readonly status?: string;
  /** Why creating this one failed. A registry that exists but never settled reports both this and `registryId`. */
  readonly createError?: string;
}

export async function init(options: Options): Promise<number> {
  const configPath = resolveConfigPath(options.config);
  if (fs.existsSync(configPath) && !options.force) {
    process.stderr.write(
      `${configPath} already exists.\n` +
        'Edit it directly, or pass --force to start over.\n',
    );
    return 1;
  }

  if (!isInteractive()) {
    const template = path.join(packageRoot(), 'config', 'migration.example.json');
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.copyFileSync(template, configPath);
    process.stdout.write(
      `Wrote ${configPath} from the template (this is not a terminal, so nothing was asked).\n` +
        'Fill in the account, regions and registry ids, then run: agent-registry-migration check\n',
    );
    return 0;
  }

  // Quiet, because failing is an expected outcome here and one this wizard explains itself: with no
  // credentials the engine logs "ERROR Unable to locate credentials", which landed directly above
  // that explanation and read like a crash.
  const identity = runEngineJson<{ account?: string; region?: string; arn?: string }>(['account'], {
    quiet: true,
  });
  process.stdout.write(
    '\nSetting up your migration. This is the only time you are asked for any of this.\n\n',
  );
  if (identity?.arn) {
    process.stdout.write(`Using credentials for ${identity.arn}\n\n`);
  } else {
    // Said here, before any question, because it changes what the rest of this wizard can do for you.
    //
    // Without credentials the last step -- reading each Preview registry to derive the target registry
    // configuration -- fails. It used to fail exactly there, after every question had been answered,
    // with "Unable to locate credentials" and no indication that the shell was the problem rather
    // than the answers. The account id also cannot be filled in for you, which is why the first
    // prompt below offers no default.
    process.stdout.write(
      'No AWS credentials found in this shell.\n' +
        'The questions below still work and the configuration will still be written, but two things\n' +
        'will not: your account id cannot be filled in for you, and the target registry configuration\n' +
        'cannot be derived from your Preview registries at the end.\n' +
        'To get both, set up credentials (for example: aws sso login, or export AWS_PROFILE) and run\n' +
        'this again. Either way, "agent-registry-migration check" needs them before a migration.\n\n',
    );
  }

  const account = await askAccount('AWS account id holding your registries', identity?.account);

  const registries: RegistryMapping[] = [];
  let index = 1;
  for (;;) {
    process.stdout.write(`\nRegistry pair ${index}\n`);
    const sourceAccount = await askAccount('  Account of the Preview registry', account);
    const sourceRegion = await askRegion('  Region of the Preview registry', identity?.region);
    const sourceRegistryId = await askRegistryId('  Preview registry id', { required: true });
    // Both sides are asked separately because both can differ: a target registry usually sits beside its
    // Preview registry, but consolidating estates across accounts or regions is a normal thing to
    // want, and the engine supports it. Defaults mean the common case is two Enter presses.
    const targetAccount = await askAccount('  Account for the target registry', sourceAccount);
    const targetRegion = await askRegion('  Region for the target registry', sourceRegion);
    const targetRegistryId = await askRegistryId(
      '  Target registry id (leave empty and I will create the new-version registry for you)',
      { required: false },
    );
    registries.push({
      id: `registry-${index}`,
      source: { accountId: sourceAccount, region: sourceRegion, registryId: sourceRegistryId },
      target: {
        accountId: targetAccount,
        region: targetRegion,
        registryId: targetRegistryId || PLACEHOLDER_TARGET,
      },
    });
    index += 1;
    if (!(await confirm('\nMigrate another registry?', false))) {
      break;
    }
  }

  const config: MigrationFile = {
    // The engine runs where you are: cross-account mappings reach out from here, they do not move
    // the deployment.
    engine: { account, region: registries[0]?.target.region ?? identity?.region },
    registries,
  };
  explainRemoteAccounts(account, registries);
  writeConfig(configPath, config);
  process.stdout.write(`\nWrote ${configPath}\n`);

  const missing = registries.filter((mapping) => mapping.target.registryId === PLACEHOLDER_TARGET);
  if (missing.length > 0) {
    await helpCreateTargetRegistries(configPath, config, missing);
    return 0;
  }

  process.stdout.write('\nNext: agent-registry-migration check\n');
  return 0;
}

/**
 * Derive each target registry's configuration from its Preview registry, create it, and record the id.
 *
 * Derive first and create second, in that order and with the payload on screen in between: the
 * derived `discoveryConfiguration` decides who may read the registry, so it is shown before it is
 * applied. What used to be manual either side of that -- running the create call, waiting for
 * READY, copying the generated id into the configuration -- this does.
 */
async function helpCreateTargetRegistries(
  configPath: string,
  config: MigrationFile,
  missing: RegistryMapping[],
  options: { readonly create?: boolean } = {},
): Promise<void> {
  const outputDir = path.resolve(path.dirname(configPath), 'new-registry-payloads');
  process.stdout.write(
    '\nYou still need a target registry to migrate into. Its configuration is derived from the\n' +
      'Preview registry, and I can create it for you once you have seen what it says.\n\n',
  );
  // Ask for JSON so the derived payload path comes back from the engine that wrote it, rather than
  // being reconstructed here from assumptions about the output directory.
  const derived = runEngineJson<DerivedTarget[]>(
    [
      'target-config',
      '--config-file',
      configPath,
      '--output-dir',
      outputDir,
      '--json',
      '--mapping',
      missing.map((mapping) => mapping.id).join(','),
    ],
    // One registry that cannot be read must not cost the others their configuration: the engine
    // reports that mapping's error and still describes the rest, and the loop below says which one
    // was left out.
    { partial: true },
  );
  if (!derived || derived.length === 0) {
    // Name the two things that actually cause this. The reason is printed above, by the engine, but
    // "Unable to locate credentials" on its own reads like a problem with the answers just given
    // rather than with the shell -- so say which it is and that nothing has been lost.
    process.stdout.write(
      '\nCould not read the Preview registries -- see the reason above. Usually it is one of two\n' +
        'things: no AWS credentials in this shell, or credentials without\n' +
        'bedrock-agentcore:GetRegistry on that registry.\n\n' +
        `Your answers are saved in ${configPath}, so nothing is lost. Either fix the credentials and\n` +
        'run "agent-registry-migration target-config" to derive the configuration, or create the target\n' +
        'registries yourself and put their ids into that file as target.registryId.\n',
    );
    return;
  }
  const pending: RegistryMapping[] = [];
  for (const mapping of missing) {
    const entry = derived.find((candidate) => candidate.mappingId === mapping.id);
    if (!entry?.payloadPath) {
      process.stdout.write(`\nCould not derive a configuration for ${mapping.id}; create it by hand.\n`);
      continue;
    }
    process.stdout.write(
      `\nFor ${mapping.id}, these are the target registry's settings, translated from your Preview\n` +
        `  registry:\n  ${entry.payloadPath}\n\n`,
    );
    // Printed with the payload rather than left to scroll past on stderr: "review this" is only
    // actionable if what to look at comes with it. These are authorizer decisions -- a field the service
    // cannot accept, or an audience still naming the Preview registry.
    for (const warning of entry.warnings ?? []) {
      process.stdout.write(`  ! ${warning}\n\n`);
    }
    pending.push(mapping);
  }

  let recorded = false;
  // --create says yes without asking, for a non-interactive or scripted run. Otherwise ask, because
  // this is the first thing either command does that writes anything to an AWS account.
  const create =
    pending.length > 0 &&
    (options.create === true ||
      (isInteractive() && (await confirm(`Create ${pending.length === 1 ? 'it' : 'them'} now?`, true))));
  if (create) {
    recorded = await createTargetRegistries(configPath, config, pending, outputDir);
  } else {
    for (const mapping of pending) {
      const entry = derived.find((candidate) => candidate.mappingId === mapping.id);
      if (entry?.command) {
        process.stdout.write(`\nTo create ${mapping.id} yourself:\n  ${entry.command}\n`);
      }
      process.stdout.write(
        '  (creation is asynchronous; wait for the registry status to reach READY before loading)\n\n',
      );
      // Only offer to take the id back when there is a terminal to answer and something to fill in.
      // Asking with no tty hangs forever, and a mapping that already has a real id has nothing to ask
      // about -- its payload was printed so it can be compared against the registry that exists.
      if (!isInteractive() || mapping.target.registryId !== PLACEHOLDER_TARGET) {
        continue;
      }
      const registryId = await ask(`  Target registry id for ${mapping.id} (empty to add it later)`);
      if (registryId) {
        const stored = (config.registries ?? []).find((candidate) => candidate.id === mapping.id);
        if (stored) {
          stored.target.registryId = registryId;
          recorded = true;
        }
      }
    }
  }
  // Only written when a registry id was actually collected. Deriving alone changes nothing, so
  // rewriting the file unconditionally -- reformatting it, and touching its mtime -- was a side
  // effect nobody asked for.
  if (recorded) {
    writeConfig(configPath, config);
  }

  const stillMissing = (config.registries ?? []).some(
    (mapping) => mapping.target.registryId === PLACEHOLDER_TARGET,
  );
  process.stdout.write(
    stillMissing
      ? `\nPut the remaining target registry ids into ${configPath}, then run: agent-registry-migration check\n`
      : '\nNext: agent-registry-migration check\n',
  );
}

/**
 * Create each pending target registry through the engine, and write the generated ids into the file.
 *
 * Returns whether the configuration now holds an id it did not before, so the caller writes the file
 * once. A registry that was created but never reached READY records its id *and* reports the reason:
 * the id is what stops the next attempt creating a second registry, so losing it is worse than
 * storing one that is not ready yet.
 */
async function createTargetRegistries(
  configPath: string,
  config: MigrationFile,
  pending: readonly RegistryMapping[],
  outputDir: string,
): Promise<boolean> {
  process.stdout.write(
    `\nCreating ${pending.length === 1 ? 'the target registry' : `${pending.length} target registries`}. ` +
      'Each one provisions a workload identity, so this\ntakes a moment.\n',
  );
  const created = runEngineJson<DerivedTarget[]>(
    [
      'target-config',
      '--config-file',
      configPath,
      '--output-dir',
      outputDir,
      '--json',
      '--create',
      '--mapping',
      pending.map((mapping) => mapping.id).join(','),
    ],
    // A create that failed for one mapping still created the others, and their ids only exist in
    // this output. Dropping it would leave real registries nobody can name, and the next run would
    // create a second one for each.
    { partial: true },
  );
  if (!created) {
    process.stdout.write(
      '\nNothing was created -- see the reason above. The derived payloads are still in\n' +
        `${outputDir}, so you can create the registries from them and put their ids into\n` +
        `${configPath} as target.registryId.\n`,
    );
    return false;
  }
  let recorded = false;
  for (const mapping of pending) {
    const entry = created.find((candidate) => candidate.mappingId === mapping.id);
    if (entry?.registryId) {
      const stored = (config.registries ?? []).find((candidate) => candidate.id === mapping.id);
      if (stored) {
        stored.target.registryId = entry.registryId;
        recorded = true;
      }
      process.stdout.write(`  ${mapping.id}: ${entry.registryId}  (${entry.status ?? 'created'})\n`);
    }
    if (entry?.createError) {
      process.stdout.write(`  ${mapping.id}: ${entry.createError}\n`);
      // Named rather than left to be looked up: this is the failure people hit, it arrives as the
      // registry's own statusReason ("Unable to create workload identity because access was
      // denied"), and the missing permission is not the one the message appears to be about.
      process.stdout.write(
        '    Creating a registry needs agent-registry:CreateRegistry and the workload-identity\n' +
          '    permissions listed in docs/iam.md.\n',
      );
    } else if (!entry?.registryId) {
      process.stdout.write(`  ${mapping.id}: no registry was created; create it by hand.\n`);
    }
  }
  return recorded;
}

// --------------------------------------------------------------------------------------------
// check
// --------------------------------------------------------------------------------------------

export function check(options: Options): number {
  const configPath = resolveConfigPath(options.config);
  const config = readConfig(configPath);
  if (options.json) {
    // --json exists so a pipeline can read the result. A configuration problem is a result too, so
    // it has to arrive on stdout as JSON -- and in the SAME shape the engine emits, or a caller that
    // reads `status` and `checks` sees nothing it recognises.
    const problem = readinessProblem(config, configPath);
    if (problem) {
      process.stdout.write(
        `${JSON.stringify(
          {
            status: 'FAIL',
            checks: [{ name: 'config.ready', status: 'FAIL', detail: problem }],
            failureCount: 1,
            warningCount: 0,
            configurationSource: `file ${configPath}`,
          },
          undefined,
          2,
        )}\n`,
      );
      return 1;
    }
  }
  assertReady(config, configPath);
  const args = [
    'check',
    ...engineArgs(config, configPath, options.glue, options.local),
    ...scopeArgs(options),
    // check reports the run you are about to make, so the write decision has to reach it. Without
    // this, `check --live` answered about a dry run -- "will NOT write to any target registry" -- which
    // is the one thing a pre-flight must never get backwards.
    ...(options.live ? ['--live', 'true'] : []),
  ];
  if (options.offline) {
    args.push('--offline');
  }
  if (options.json) {
    args.push('--json');
  }
  return runEngine(args).status;
}

/**
 * Why this configuration is not ready to run, or undefined when it is.
 *
 * Split from the throwing form so `--json` can report the same problem as data.
 */
function readinessProblem(config: MigrationFile, configPath: string): string | undefined {
  if (mappingCount(config) === 0) {
    return (
      `${configPath} has no registries to migrate. ` +
      'Add a source/target pair, or run "agent-registry-migration init --force" to start over.'
    );
  }
  const placeholders = (config.registries ?? []).filter((mapping) =>
    String(mapping.target?.registryId ?? '').startsWith('<'),
  );
  if (placeholders.length > 0) {
    return (
      `${configPath} still has a placeholder target registry id for: ` +
      `${placeholders.map((mapping) => mapping.id).join(', ')}. ` +
      'Create the target registry, then put its id in as target.registryId. To see the settings to ' +
      'create it with, translated from the Preview registry, run ' +
      '"agent-registry-migration target-config".'
    );
  }
  return undefined;
}

/** Refuse to run against a configuration that is still holding a placeholder. */
function assertReady(config: MigrationFile, configPath: string): void {
  const problem = readinessProblem(config, configPath);
  if (problem) {
    throw new CliError(problem);
  }
}

/**
 * Derive the target registry configuration for any mapping, at any time.
 *
 * `init` does this as part of first setup, but it is not a one-off need: adding a mapping later, or
 * rebuilding a registry, wants the same translation. Keeping it only inside `init` meant the answer
 * was reachable exactly once, and only by overwriting the configuration to get back to it.
 *
 * Reads the Preview registries and writes the payloads locally. It creates nothing: the authorizer
 * on a target registry decides who may read it, so applying it stays the operator's call.
 */
export async function targetConfig(options: Options): Promise<number> {
  const configPath = resolveConfigPath(options.config);
  const config = readConfig(configPath);
  if (mappingCount(config) === 0) {
    throw new CliError(`${configPath} has no registries. Run "agent-registry-migration init" first.`);
  }
  const missing = (config.registries ?? []).filter((mapping) =>
    String(mapping.target?.registryId ?? '').startsWith('<'),
  );
  // With every target already filled in there is nothing to chase, but the translation is still
  // worth printing -- that is how you check an existing registry matches its source.
  await helpCreateTargetRegistries(configPath, config, missing.length > 0 ? missing : (config.registries ?? []), {
    create: options.create,
  });
  return 0;
}

// --------------------------------------------------------------------------------------------
// run
// --------------------------------------------------------------------------------------------

/**
 * Everything the three migration commands need, resolved once.
 *
 * `run`, `extract` and `load` are the same machinery: the same configuration, the same engine
 * arguments, and either the local stages or the deployed Glue jobs. Resolving it in one place is what
 * keeps `extract` + `load` and a single `run` from drifting into two behaviours.
 */
interface Context {
  readonly configPath: string;
  readonly config: MigrationFile;
  readonly shared: string[];
  /** Glue job names, when running on the deployed engine. */
  readonly stages?: { extract: string; load: string };
}

function context(options: Options): Context {
  const configPath = resolveConfigPath(options.config);
  const config = readConfig(configPath);
  assertReady(config, configPath);
  return {
    configPath,
    config,
    shared: [...engineArgs(config, configPath, options.glue, options.local), ...scopeArgs(options)],
    // Resolved before any work, so a missing deployment is reported up front rather than after the
    // checks have run against a configuration that is not going to be used.
    stages: options.glue ? glueStages(config) : undefined,
  };
}

function header(
  where: Context,
  options: Options,
  lines: Record<string, string>,
): void {
  const rows = {
    configuration: where.configPath,
    registries: String(mappingCount(where.config)),
    staging: options.glue
      ? 'the deployed engine'
      : describeStaging(where.config, where.configPath, options.local),
    ...lines,
  };
  const width = Math.max(...Object.keys(rows).map((key) => key.length));
  const body = Object.entries(rows)
    .map(([key, value]) => `  ${key.padEnd(width)} : ${value}`)
    .join('\n');
  process.stdout.write(`${body}\n\n`);
}

function runChecks(where: Context, options: Options): number {
  return runEngine(['check', ...where.shared, ...(options.live ? ['--live', 'true'] : [])]).status;
}

function extractStage(where: Context, options: Options, runId: string): number {
  return where.stages
    ? runEngine([
        'glue-run',
        '--job',
        where.stages.extract,
        '--run-id',
        runId,
        // The scope has to travel to the job as well. Without it the job falls back to the deployed
        // loadMode, so --incremental would print in the header and be ignored in the run.
        ...scopeArgs(options),
        ...regionArg(where.config),
      ]).status
    : runEngine(['extract', ...where.shared, '--run-id', runId]).status;
}

function loadStage(where: Context, options: Options, runId: string): number {
  const live = options.live ? 'true' : 'false';
  return where.stages
    ? runEngine([
        'glue-run',
        '--job',
        where.stages.load,
        '--run-id',
        runId,
        '--live',
        live,
        ...scopeArgs(options),
        ...regionArg(where.config),
      ]).status
    : runEngine(['load', ...where.shared, '--run-id', runId, '--live', live]).status;
}

// --------------------------------------------------------------------------------------------
// extract
// --------------------------------------------------------------------------------------------

/**
 * Read the preview registries into staging, and nothing else.
 *
 * The first half of a migration on its own, for the flow where reading and writing are separate
 * decisions taken at different times -- possibly by different people. It writes nothing to the target registry and
 * cannot: no `--live` reaches it. What it hands back is the run id, which is what `load` and `report`
 * take, so the id comes from the command that produced it rather than from the tail of a longer one.
 */
export function extract(options: Options): number {
  const where = context(options);
  const runId = newRunId();

  process.stdout.write('Reading the Preview registries. Nothing is written to the target registry.\n');
  header(where, options, { covering: describeScope(options), 'run id': runId });

  const checked = runChecks(where, options);
  if (checked !== 0) {
    process.stderr.write('\nStopped: the checks above failed, so nothing was read.\n');
    return checked;
  }

  const extracted = extractStage(where, options, runId);
  if (extracted !== 0) {
    process.stderr.write('\nExtraction failed. Nothing was written to the target registry.\n');
    return extracted;
  }

  process.stdout.write('\n');
  runEngine(['report', ...where.shared, '--run-id', runId]);
  process.stdout.write(
    `\nExtracted, and nothing has been written to the target registry. This run id is ${runId}\n\n` +
      'Review it, then load it:\n' +
      `  agent-registry-migration load${whereFlag(options)} --dry-run   # transform and report, still writing nothing\n` +
      `  agent-registry-migration load${whereFlag(options)} --live      # create the target records\n\n` +
      `Both default to this extract, the most recent one. Pass --run-id ${runId} to be explicit,\n` +
      'or name an older run id to load that one instead.\n',
  );
  return 0;
}

// --------------------------------------------------------------------------------------------
// load
// --------------------------------------------------------------------------------------------

/**
 * Transform and load an extract that already exists.
 *
 * Defaults to the most recent extract, because that is almost always the one just reviewed, while
 * `--run-id` keeps the choice explicit for anything else -- an older extract, or one someone else
 * produced.
 */
export function load(options: Options): number {
  const where = context(options);
  const runId = options.runId ?? latestRunId(where.shared);

  process.stdout.write(
    options.live
      ? 'LIVE -- records will be created in the target registries\n'
      : 'Dry run -- nothing will be written to the target registry\n',
  );
  header(where, options, {
    'run id': `${runId}${options.runId ? '' : ' (the most recent extract)'}`,
  });

  const checked = runChecks(where, options);
  if (checked !== 0) {
    process.stderr.write('\nStopped: the checks above failed, so nothing was written.\n');
    return checked;
  }

  process.stdout.write(
    options.live ? 'Creating the target records...\n' : 'Transforming and reporting...\n',
  );
  const loaded = loadStage(where, options, runId);

  process.stdout.write('\n');
  runEngine(['report', ...where.shared, '--run-id', runId]);
  if (loaded === 0 && !options.live) {
    process.stdout.write(
      '\nNothing was written to the target registry. When the report above looks right:\n' +
        `  agent-registry-migration load${whereFlag(options)} --live --run-id ${runId}\n`,
    );
  }
  return loaded;
}

// --------------------------------------------------------------------------------------------
// run
// --------------------------------------------------------------------------------------------

/**
 * The whole migration in one command: check, read, transform, load, report.
 *
 * Kept alongside `extract` and `load` because most migrations are one sitting -- and someone who
 * already knows what they are migrating should not have to run two commands to do it.
 */
export function run(options: Options): number {
  const where = context(options);
  const resumeId = options.resume ?? (options.resumeLatest ? latestRunId(where.shared) : undefined);
  const runId = resumeId ?? newRunId();

  process.stdout.write(
    options.live
      ? 'LIVE RUN -- records will be created in the target registries\n'
      : 'Dry run -- nothing will be written to the target registry\n',
  );
  header(where, options, {
    covering: describeScope(options),
    'run id': `${runId}${resumeId ? ' (reusing the records you already reviewed)' : ''}`,
  });
  if (options.incremental && resumeId) {
    // Which records a run covers is decided when they are read, and --resume skips reading.
    process.stdout.write(
      'Note: --incremental has no effect with --resume. The records were selected when this run\n' +
        '      was extracted; this loads exactly those.\n\n',
    );
  }

  const checked = runChecks(where, options);
  if (checked !== 0) {
    process.stderr.write('\nStopped: the checks above failed, so nothing was read or written.\n');
    return checked;
  }

  if (!resumeId) {
    process.stdout.write('\nReading the Preview registries (read-only)...\n');
    const extracted = extractStage(where, options, runId);
    if (extracted !== 0) {
      process.stderr.write('\nStopped during extraction. Nothing was written to the target registry.\n');
      return extracted;
    }
  }

  process.stdout.write(
    options.live ? '\nCreating the target records...\n' : '\nTransforming and reporting...\n',
  );
  const loaded = loadStage(where, options, runId);

  process.stdout.write('\n');
  runEngine(['report', ...where.shared, '--run-id', runId]);

  if (loaded === 0 && !options.live) {
    process.stdout.write(
      '\nNothing was written to the target registry. When the report above looks right, load the same records:\n' +
        `  agent-registry-migration load${whereFlag(options)} --live\n` +
        `\n(that loads this run, ${runId}, being the most recent extract. Pass --run-id to name it,\n` +
        ' or use `run --live --resume` to do the whole thing again in one command.)\n',
    );
  }
  return loaded;
}

/**
 * The most recent extract that is ready to load, for a bare `--resume`.
 *
 * Asked of the engine rather than worked out here: it is the side that can see the staging
 * location, whether that is a directory or a bucket, and it already skips a run whose extraction
 * failed instead of offering it.
 */
function latestRunId(shared: string[]): string {
  const result = runEngine(['latest-run', ...shared], { capture: true });
  const runId = result.stdout.trim().split('\n').pop()?.trim();
  if (result.status !== 0 || !runId) {
    throw new CliError(
      'No extract that is ready to load was found, so there is nothing to load.\n' +
        'Read the preview registries first:\n' +
        '  agent-registry-migration extract\n' +
        'That prints a run id, which `load` then defaults to.',
    );
  }
  return runId;
}

function regionArg(config: MigrationFile): string[] {
  return config.engine?.region ? ['--region', config.engine.region] : [];
}

/**
 * The flag that decided where this run happened, for use in a suggested follow-up command.
 *
 * Without it the next step printed after `extract --glue` was `load --dry-run`, which runs the load
 * *locally* against a run staged in the deployed bucket -- a different place from the one the
 * sentence above it was talking about. Suggestions have to stay in the same place as the command
 * that printed them.
 */
function whereFlag(options: Options): string {
  if (options.glue) {
    return ' --glue';
  }
  if (options.local) {
    return ' --local';
  }
  return '';
}

function glueStages(config: MigrationFile): { extract: string; load: string } {
  const info = engineInfo(config);
  const extract = info?.outputs?.['ExtractJobName'];
  const load = info?.outputs?.['TransformLoadJobName'];
  if (!extract || !load) {
    throw new CliError(
      `No deployed engine found for stack ${stackName(config)}.\n` +
        'Run "agent-registry-migration deploy" first, or drop --glue to run it here.',
    );
  }
  return { extract, load };
}

// --------------------------------------------------------------------------------------------
// report
// --------------------------------------------------------------------------------------------

export function report(options: Options): number {
  const configPath = resolveConfigPath(options.config);
  const config = readConfig(configPath);
  const args = ['report', ...engineArgs(config, configPath, options.glue, options.local)];
  if (options.runId) {
    args.push('--run-id', options.runId);
  }
  if (options.json) {
    args.push('--json');
  }
  return runEngine(args).status;
}

// --------------------------------------------------------------------------------------------
// deploy
// --------------------------------------------------------------------------------------------

/**
 * Say plainly when the bucket this deploy would create already exists, instead of letting
 * CloudFormation fail the whole stack create several minutes in.
 *
 * Returns the message to print (and refuse the deploy over), or `undefined` when there is no
 * collision -- including when the check itself could not run (no credentials, no network): that
 * is not this function's problem to report, `cdk deploy` will surface a real credentials issue
 * on its own, with its own message.
 */
function checkBucketCollision(config: MigrationFile): string | undefined {
  const bucket = stagingBucketName(stackName(config), config.engine?.account, config.engine?.region);
  const info = bucketInfo(bucket, config.engine?.region);
  if (!info || !info.exists) {
    return undefined;
  }
  if (info.ownedByCaller && info.applicationTag === 'AgentRegistryMigration') {
    // The expected way to reach this: `destroy` deletes the stack but keeps the bucket (its
    // reports, watermarks and id maps) unless --delete-data is also given.
    return (
      `A staging bucket from a previous deployment already exists at s3://${bucket}.\n` +
      'This deploy would create a bucket with that exact name, which CloudFormation cannot do -- ' +
      'S3 bucket names are global, and an existing one is never reused, only refused.\n\n' +
      'This is what "destroy" without --delete-data leaves behind on purpose, so its reports, ' +
      'watermarks and id maps survive a torn-down engine. To continue:\n' +
      '  - delete that bucket and everything in it, then deploy again:\n' +
      `      aws s3 rb s3://${bucket} --force\n` +
      '  - or set a different engine.stackName in your configuration file to deploy a second, ' +
      'independent engine alongside it (this one keeps its own bucket, watermarks and id maps).\n'
    );
  }
  if (info.ownedByCaller) {
    // Exists, readable, but never tagged by this tool -- an unrelated bucket that happens to
    // collide with the deterministic name. Still not something to delete on the caller's behalf.
    return (
      `s3://${bucket} already exists in this account, but was not created by this tool ` +
      '(it carries no Application=AgentRegistryMigration tag). This deploy would create a bucket ' +
      'with that exact name, which CloudFormation refuses when one already exists.\n' +
      'Rename or remove that bucket, or set a different engine.stackName so this engine names ' +
      'its bucket differently.\n'
    );
  }
  // Exists, but this credential cannot read it -- owned by a different AWS account entirely.
  return (
    `s3://${bucket} already exists and belongs to a different AWS account than this credential's. ` +
    'S3 bucket names are unique across all of AWS, not just this account, so this deploy cannot ' +
    'use that name.\n' +
    'Set a different engine.stackName in your configuration file and deploy again.\n'
  );
}

export function deploy(options: Options): number {
  const configPath = resolveConfigPath(options.config);
  const config = readConfig(configPath);
  assertReady(config, configPath);

  // Refuse before `cdk deploy` runs, rather than after it has left something behind.
  //
  // The stack changes IAM, so the CDK toolkit asks for confirmation. With no TTY to ask at it
  // cannot, and what it leaves is not nothing: creating the changeset has already created the
  // stack shell in REVIEW_IN_PROGRESS, carrying the EnableTerminationProtection this stack sets.
  // That combination cannot delete itself, so the *next* deploy fails on
  // "cannot be deleted while TerminationProtection is enabled", and so does every one after it.
  // `--yes` is the non-interactive equivalent of answering the prompt, so ask for it up front.
  if (!options.yes && !isInteractive()) {
    process.stderr.write(
      'deploy needs a terminal to confirm the IAM changes this stack makes, and there is not one ' +
        'here (no TTY).\n' +
        'Re-run with --yes to confirm them up front:\n' +
        '  agent-registry-migration deploy --yes\n\n' +
        'Nothing was deployed. Stopping here on purpose: without a confirmation the CDK toolkit ' +
        'creates the stack in REVIEW_IN_PROGRESS and then cannot remove it again, which blocks ' +
        'every later deploy until the stack is deleted by hand.\n',
    );
    return 1;
  }

  // A stack name is unique per account+region, so a second person deploying with the same
  // (default) stack name in the same account+region targets this exact stack -- CloudFormation
  // already prevents a duplicate. What it does not do is tell them that before they run it, so
  // check first and say which case this is.
  let existing = engineInfo(config);
  if (existing?.status === 'REVIEW_IN_PROGRESS') {
    // The wedge above, left by an earlier deploy (or by an interrupted one). The stack holds no
    // resources -- a changeset was created for it and never executed -- so removing it is the
    // only way forward, and doing it here means a customer does not have to reach for the
    // CloudFormation console to get unstuck.
    process.stdout.write(
      `Stack ${existing.stackName} is in REVIEW_IN_PROGRESS: an earlier deploy created it and ` +
        'never finished, so it holds no resources and cannot be updated.\nRemoving it so this ' +
        'deploy can create the engine properly.\n\n',
    );
    const cleared = runEngine([
      'clear-pending-stack',
      '--stack-name',
      stackName(config),
      ...regionArg(config),
    ]);
    if (cleared.status !== 0) {
      return cleared.status;
    }
    existing = undefined;
  }
  if (existing) {
    process.stdout.write(
      `Joining an existing installation: stack ${existing.stackName} was already deployed here` +
        (existing.creationTime ? ` on ${existing.creationTime}` : '') +
        (existing.lastUpdatedTime ? `, last updated ${existing.lastUpdatedTime}` : '') +
        '.\n' +
        'This will update it in place -- the staging bucket, watermarks and id maps under it are\n' +
        'shared by everyone deploying into this account and region, so records already migrated\n' +
        'stay recognised rather than duplicated.\n\n',
    );
  } else {
    // No stack under this name here -- but the bucket it would create is named deterministically
    // from stackName + account + region (see stagingBucketName in lib/config.ts), and S3 bucket
    // names are global and never adopted: if one already exists, CloudFormation does not reuse
    // it, it fails the entire stack create with BucketAlreadyOwnedByYou. The most common way to
    // reach this state is `destroy` without --delete-data, which deletes the stack but
    // deliberately retains the bucket -- so check first and say so plainly, rather than let a
    // `deploy` fail several minutes in with a raw CloudFormation rollback.
    const collision = checkBucketCollision(config);
    if (collision) {
      process.stderr.write(collision);
      return 1;
    }
    process.stdout.write(
      'Deploying the migration engine: an S3 staging bucket, two Glue jobs and the\n' +
        'configuration parameters they read. No registry is touched by a deploy.\n\n',
    );
  }
  const approval = options.yes ? ['--require-approval', 'never'] : [];
  const status = runCdk(['deploy', '--all', ...approval], configPath, config.engine?.region);
  if (status !== 0) {
    return status;
  }

  // Record what the deployment created, so no command has to be told the bucket name and there is
  // no deploy -> edit -> deploy loop.
  const info = engineInfo(config);
  const bucket = info?.outputs?.['StagingBucketName'];
  const prefix = info?.outputs?.['ConfigurationParameterPrefix'];
  if (bucket) {
    config.engine = { ...config.engine, stagingBucket: bucket, ...(prefix ? { parameterPrefix: prefix } : {}) };
    writeConfig(configPath, config);
    process.stdout.write(`\nRecorded the staging bucket in ${configPath}: s3://${bucket}\n`);
  }

  if (config.engine?.createIamRoles === false && !bucket) {
    // Without the bucket there is nowhere to publish, and in this mode the stack did not publish
    // either -- so the jobs have no entrypoints. Saying so here beats an ImportError in a Glue run
    // an hour later, which is what silently falling through used to produce.
    process.stderr.write(
      '\nDeployed, but the staging bucket could not be read from the stack outputs, so the Glue job\n' +
        'artifacts were NOT published. With engine.createIamRoles false the stack does not publish\n' +
        'them either, so a run would fail on a missing app/extract.py. Check your credentials and\n' +
        'region can read the stack, then run deploy again.\n',
    );
    return 1;
  }
  if (config.engine?.createIamRoles === false && bucket) {
    // The stack cannot upload its own job artifacts in this mode: the construct that would do it
    // provisions a Lambda, and therefore an IAM role.
    process.stdout.write('Publishing the Glue job artifacts...\n');
    const published = runEngine([
      'publish-artifacts',
      '--staging-bucket',
      bucket,
      '--app-dir',
      path.join(packageRoot(), 'glue'),
      '--wheel-dir',
      path.join(packageRoot(), 'build', 'glue-lib'),
      // Every other engine call is told the region; without it this one uploads wherever the
      // ambient session points, which is not necessarily where the stack was just deployed.
      ...regionArg(config),
    ]);
    if (published.status !== 0) {
      return published.status;
    }
  }

  process.stdout.write('\nNext: agent-registry-migration run --glue\n');
  return 0;
}

// --------------------------------------------------------------------------------------------
// destroy
// --------------------------------------------------------------------------------------------

export function destroy(options: Options): number {
  const configPath = resolveConfigPath(options.config);
  // A configuration this command was explicitly pointed at must exist. Falling back to the
  // defaults here would mean a mistyped --config silently retargets the teardown at the
  // DEFAULT stack name -- a stack the caller never named, in whatever region the ambient
  // credentials point at. With --yes --delete-data that deletes a stack and empties a bucket
  // on the strength of a typo, so this is refused rather than guessed.
  if (options.config && !fs.existsSync(configPath)) {
    throw new CliError(
      `No configuration at ${configPath}.\n` +
        'Check the --config path. To tear down a deployment whose configuration is gone, run ' +
        '"agent-registry-migration destroy" without --config and it will use the default stack ' +
        'name, or pass the name yourself with --config pointing at a file that sets engine.stackName.',
    );
  }
  // Tearing down must still work when the configuration is already gone -- deleted, or on a
  // different machine from the one that deployed -- as long as no specific file was named. The
  // file only supplies the stack name and region, so fall back to the defaults rather than
  // refusing to clean up.
  const config = fs.existsSync(configPath) ? readConfig(configPath) : {};
  const args = ['destroy', '--stack-name', stackName(config)];
  if (config.engine?.region) {
    args.push('--region', config.engine.region);
  }
  if (options.yes) {
    args.push('--yes');
  }
  if (options.deleteData) {
    args.push('--delete-data');
  }
  if (options.keepReports) {
    args.push('--keep-reports');
  }
  const status = runEngine(args).status;

  if (
    status === 0 &&
    options.yes &&
    options.deleteData &&
    !options.keepReports &&
    fs.existsSync(configPath) &&
    config.engine?.stagingBucket
  ) {
    const { stagingBucket, ...rest } = config.engine;
    config.engine = rest;
    writeConfig(configPath, config);
    process.stdout.write(`\nRemoved the deleted bucket from ${configPath}.\n`);
  }
  return status;
}
