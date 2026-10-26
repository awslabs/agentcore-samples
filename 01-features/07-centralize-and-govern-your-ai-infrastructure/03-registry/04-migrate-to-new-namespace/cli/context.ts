/**
 * Everything the commands share: where the configuration is, where Python is, and how to reach
 * the migration engine.
 *
 * The point of this module is that a user answers each question once. The configuration file is
 * the single source of truth, its location is resolved the same way by every command, and the
 * staging location and Glue job names are read from the deployment rather than typed again.
 */
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as readline from 'node:readline';

/** Config file names looked for, in order, when `--config` is not given. */
export const CONFIG_CANDIDATES = ['migration.config.json', 'config/migration.json'];

/** Where a local run stages its data when no staging bucket has been deployed. */
export const DEFAULT_STAGING_DIRECTORY = 'migration-runs';

export const DEFAULT_STACK_NAME = 'AgentRegistryMigrationEngine';

export interface RegistryEndpoint {
  accountId?: string;
  region?: string;
  registryId?: string;
  roleArn?: string;
  externalId?: string;
}

export interface RegistryMapping {
  id: string;
  source: RegistryEndpoint;
  target: RegistryEndpoint;
}

export interface MigrationFile {
  engine?: {
    account?: string;
    region?: string;
    stackName?: string;
    stagingBucket?: string;
    stagingDirectory?: string;
    parameterPrefix?: string;
    createIamRoles?: boolean;
    [key: string]: unknown;
  };
  runtime?: { load?: Record<string, unknown>; transform?: Record<string, unknown> };
  registries?: RegistryMapping[];
  [key: string]: unknown;
}

export class CliError extends Error {
  /**
   * Process exit code.
   *
   * 2 marks a usage error -- a command line that could never work, whatever the state of the
   * account -- which is what the Python entrypoint already returns for the same condition. 1 is
   * everything else: a real failure of something that was worth attempting.
   */
  readonly exitCode: number;

  constructor(message: string, exitCode = 1) {
    super(message);
    this.exitCode = exitCode;
  }
}

/** Root of this package, whether it is a checkout or installed under node_modules. */
export function packageRoot(): string {
  // Compiled to <root>/dist/cli/context.js, so the root is two directories up.
  return path.resolve(__dirname, '..', '..');
}

/** The directory holding the two Glue stage entrypoints and the Python library. */
export function glueDir(): string {
  const candidate = path.join(packageRoot(), 'glue');
  if (!fs.existsSync(path.join(candidate, 'common', 'migration_common'))) {
    throw new CliError(
      `The migration engine is missing from this installation (expected ${candidate}). ` +
        'Reinstall the package.',
    );
  }
  return candidate;
}

export function resolveConfigPath(explicit?: string): string {
  if (explicit) {
    return path.resolve(explicit);
  }
  for (const candidate of CONFIG_CANDIDATES) {
    const resolved = path.resolve(process.cwd(), candidate);
    if (fs.existsSync(resolved)) {
      return resolved;
    }
  }
  return path.resolve(process.cwd(), CONFIG_CANDIDATES[0]!);
}

export function readConfig(configPath: string): MigrationFile {
  if (!fs.existsSync(configPath)) {
    throw new CliError(
      `No configuration at ${configPath}.\n` +
        'Run "agent-registry-migration init" once to create it.',
    );
  }
  try {
    return JSON.parse(fs.readFileSync(configPath, 'utf8')) as MigrationFile;
  } catch (error) {
    throw new CliError(`${configPath} is not valid JSON: ${(error as Error).message}`);
  }
}

export function writeConfig(configPath: string, config: MigrationFile): void {
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, 'utf8');
}

/**
 * Where this configuration's runs are staged, and the argument that selects it.
 *
 * A deployed engine publishes its bucket into the configuration (see `deploy`), so the choice is
 * made by what exists rather than by a flag the user has to remember.
 */
export function stagingArguments(
  config: MigrationFile,
  configPath: string,
  forceLocal = false,
): string[] {
  const bucket = config.engine?.stagingBucket;
  if (bucket && !forceLocal) {
    return ['--staging-bucket', bucket];
  }
  return ['--local-dir', stagingDirectory(config, configPath)];
}

export function stagingDirectory(config: MigrationFile, configPath: string): string {
  const configured = config.engine?.stagingDirectory;
  return configured
    ? path.resolve(path.dirname(configPath), configured)
    : path.resolve(path.dirname(configPath), DEFAULT_STAGING_DIRECTORY);
}

export function describeStaging(
  config: MigrationFile,
  configPath: string,
  forceLocal = false,
): string {
  const bucket = config.engine?.stagingBucket;
  return bucket && !forceLocal ? `s3://${bucket}` : stagingDirectory(config, configPath);
}

export function stackName(config: MigrationFile): string {
  return config.engine?.stackName ?? DEFAULT_STACK_NAME;
}

function pythonCandidates(): string[] {
  const configured = process.env.PYTHON;
  return configured ? [configured] : ['python3', 'python'];
}

let cachedPython: string | undefined;

// What the local interpreter has to be able to do, rather than what version it has to be. The
// requirement is really "an SDK carrying both service models": `bedrock-agentcore-control` for the
// Preview source and `agent-registry-control` for the target. The latter first shipped in
// botocore 1.43.66, which needs Python >= 3.10 -- but an operator who has registered the model
// another way (AWS_DATA_PATH, ~/.aws/models) is equally able to run this, and a version comparison
// would turn that working setup away. So probe the capability and let the message name the versions.
const PYTHON_PROBE = [
  'import boto3, botocore.session',
  'a = botocore.session.get_session().get_available_services()',
  "missing = [s for s in ('bedrock-agentcore-control', 'agent-registry-control') if s not in a]",
  'assert not missing, "botocore %s has no service model for: %s" % (botocore.__version__, ", ".join(missing))',
].join('\n');

/** Locate a Python interpreter whose boto3 models both registry APIs, or explain what to install. */
export function pythonBin(): string {
  if (cachedPython) {
    return cachedPython;
  }
  const problems: string[] = [];
  for (const candidate of pythonCandidates()) {
    const probe = spawnSync(candidate, ['-c', PYTHON_PROBE], {
      encoding: 'utf8',
    });
    if (probe.status === 0) {
      cachedPython = candidate;
      return candidate;
    }
    if (probe.error) {
      problems.push(`${candidate}: not found`);
    } else {
      problems.push(`${candidate}: ${(probe.stderr ?? '').trim().split('\n').pop()}`);
    }
  }
  throw new CliError(
    'This tool needs Python 3.10+ with boto3 1.43.66 or newer, which is how it talks to both\n' +
      '  registry APIs. Older SDKs have no model for the target control plane, so a load would fail\n' +
      "  part-way through with UnknownServiceError: 'agent-registry-control'.\n" +
      `  Tried: ${problems.join('; ')}\n` +
      "  Install with: python3 -m pip install 'boto3>=1.43.66'\n" +
      '  Or point at a specific interpreter: PYTHON=/path/to/python3',
  );
}

export interface PythonResult {
  status: number;
  stdout: string;
}

/**
 * Run the migration engine.
 *
 * `capture` returns stdout instead of streaming it, for the few commands whose output the CLI
 * reads rather than shows.
 */
/**
 * Run the Python engine.
 *
 * `quiet` captures the engine's stderr instead of letting it through, and is only for calls whose
 * failure the caller reports better itself. The identity probe at the start of `init` is the case:
 * with no credentials it logs "ERROR Unable to locate credentials", which arrived immediately above
 * the wizard's own explanation of that exact situation -- reading like a crash, and saying less.
 * Every other call inherits stderr, because for those the engine's message is the best one available.
 */
export function runEngine(
  args: string[],
  options: { capture?: boolean; quiet?: boolean } = {},
): PythonResult {
  const glue = glueDir();
  const errorStream = options.quiet ? 'pipe' : 'inherit';
  const result = spawnSync(pythonBin(), ['-m', 'migration_common', ...args], {
    stdio: options.capture ? ['inherit', 'pipe', errorStream] : ['inherit', 'inherit', errorStream],
    encoding: 'utf8',
    env: {
      ...process.env,
      PYTHONPATH: [path.join(glue, 'common'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      PYTHONUNBUFFERED: '1',
    },
  });
  if (result.error) {
    throw new CliError(`Could not run the migration engine: ${result.error.message}`);
  }
  return { status: result.status ?? 1, stdout: result.stdout ?? '' };
}

/**
 * Run the engine and parse its JSON output, or return undefined when it failed.
 *
 * `partial` keeps the output of a command that reported a failure and still printed usable JSON.
 * Commands that work per mapping do exactly that -- one registry that cannot be read or created
 * exits non-zero while the rest of the report is complete and correct -- and discarding it would
 * turn one bad mapping into no results at all, which for a create means losing the ids of the
 * registries that *were* created.
 */
export function runEngineJson<T>(
  args: string[],
  options: { quiet?: boolean; partial?: boolean } = {},
): T | undefined {
  const result = runEngine(args, { capture: true, quiet: options.quiet });
  if (result.status !== 0 && !options.partial) {
    return undefined;
  }
  try {
    return JSON.parse(result.stdout) as T;
  } catch {
    return undefined;
  }
}

export interface EngineInfo {
  stackName: string;
  status: string;
  creationTime?: string | null;
  lastUpdatedTime?: string | null;
  outputs: Record<string, string>;
}

export function engineInfo(config: MigrationFile): EngineInfo | undefined {
  const args = ['engine-info', '--stack-name', stackName(config)];
  if (config.engine?.region) {
    args.push('--region', config.engine.region);
  }
  return runEngineJson<EngineInfo>(args);
}

export interface BucketInfo {
  exists: boolean;
  accessible?: boolean;
  ownedByCaller?: boolean;
  applicationTag?: string | null;
}

/**
 * Whether a bucket name is already taken, and by whom -- undefined only when the check itself
 * could not run (no credentials, no network). `deploy` calls this ahead of a bucket name it is
 * about to create, because S3 names are global and never adopted: if one already exists,
 * CloudFormation fails the whole stack create rather than reusing it.
 */
export function bucketInfo(bucket: string, region?: string): BucketInfo | undefined {
  const args = ['bucket-info', '--bucket', bucket];
  if (region) {
    args.push('--region', region);
  }
  return runEngineJson<BucketInfo>(args);
}

/** Run the CDK toolkit against this package's app, with the user's config file. */
export function runCdk(args: string[], configPath: string, region?: string): number {
  const root = packageRoot();
  const app = path.join(root, 'dist', 'bin', 'cdk-app.js');
  if (!fs.existsSync(app)) {
    throw new CliError(
      `The CDK app is not built (expected ${app}). From a checkout, run: npm run build`,
    );
  }
  const cdk = resolveCdkBin();
  // The --app value is a *command line*, which the CDK toolkit runs through a shell -- so the path
  // has to be quoted. Unquoted, any installation path containing a space ("~/My Projects/...", or
  // anything under "Application Support") produced a CDK app command that resolved to the wrong
  // file, which is easy to hit on macOS and impossible to diagnose from the error.
  const result = spawnSync(cdk.command, [...cdk.prefix, '--app', `node "${app}"`, ...args], {
    stdio: 'inherit',
    cwd: root,
    env: {
      ...process.env,
      MIGRATION_CONFIG: configPath,
      ...(region ? { AWS_REGION: region, CDK_DEFAULT_REGION: region } : {}),
    },
  });
  if (result.error) {
    throw new CliError(`Could not run the AWS CDK toolkit: ${result.error.message}`);
  }
  return result.status ?? 1;
}

function resolveCdkBin(): { command: string; prefix: string[] } {
  const local = path.join(packageRoot(), 'node_modules', '.bin', 'cdk');
  if (fs.existsSync(local)) {
    return { command: local, prefix: [] };
  }
  return { command: process.platform === 'win32' ? 'npx.cmd' : 'npx', prefix: ['--yes', 'aws-cdk'] };
}

/** A timestamped, sortable run id, matching what the engine generates for itself. */
export function newRunId(): string {
  const now = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const suffix = Math.random().toString(16).slice(2, 10);
  return `${now}-${suffix}`;
}

export async function ask(question: string, fallback?: string): Promise<string> {
  const prompt = fallback ? `${question} [${fallback}]: ` : `${question}: `;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = await new Promise<string>((resolve) => rl.question(prompt, resolve));
    return answer.trim() || fallback || '';
  } finally {
    rl.close();
  }
}

export async function confirm(question: string, fallback = false): Promise<boolean> {
  const answer = await ask(`${question} (y/n)`, fallback ? 'y' : 'n');
  return answer.toLowerCase().startsWith('y');
}

export function isInteractive(): boolean {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY);
}


