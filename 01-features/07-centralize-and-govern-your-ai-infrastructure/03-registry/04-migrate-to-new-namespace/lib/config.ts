/**
 * Migration configuration: types, defaults, and validation.
 *
 * Loads and validates the customer's `migration.json`, applies defaults, and resolves each
 * registry mapping's cross-account access (deriving the generated access-role ARN + external
 * id when a mapping is in another account and no `roleArn` is supplied). The Preview and target
 * API wire contracts are not customer-overridable: they are read from the shared
 * `api-adapter.json` inside the Python package and validated here, so a future target API change is a
 * controlled edit to one file that both the stack and the jobs read.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';

// The single definition of the API contract, shared with the Python jobs (see the comment on
// DEFAULT_PREVIEW_API below). Imported rather than duplicated so a local run and a deployed run
// cannot disagree; validated by validateApiEndpoint/validate*ApiContract before it is published.
import * as apiAdapterFile from '../glue/common/migration_common/adapter/api-adapter.json';

export type LoadMode = 'FULL' | 'INCREMENTAL';

export interface EngineConfig {
  readonly account?: string;
  readonly region?: string;
  readonly accessStackRegion?: string;
  readonly partition: string;
  readonly stackName: string;
  readonly deploymentId: string;
  readonly parameterPrefix: string;
  readonly stagingRetentionDays: number;
  readonly reportRetentionDays: number;
  /** AWS Glue worker size for both jobs. `G.1X` is the smallest a Glue 5.0 batch job accepts. */
  readonly glueWorkerType: string;
  /** Workers per job. Two is the API minimum; the jobs are single-threaded and use one. */
  readonly glueNumberOfWorkers: number;
  readonly glueTimeoutMinutes: number;
  readonly terminationProtection: boolean;
  readonly glueRoleName?: string;
  /**
   * Whether this solution provisions IAM roles. `true` (default) is the convenient
   * internal-testing mode: the stack creates the Glue execution role, grants it exactly what
   * the jobs need, and uploads job artifacts with a CDK BucketDeployment.
   *
   * Set to `false` for production launches where the customer creates roles themselves. In
   * that mode the stack creates **no IAM roles at all**: it imports the Glue execution role
   * from `glueRoleArn` (or the `GlueExecutionRoleArn` CloudFormation parameter), skips every
   * role/policy grant, and skips the BucketDeployment (whose custom resource would itself
   * create a Lambda role) -- `agent-registry-migration deploy` publishes the job artifacts
   * instead, using the deployer's own credentials.
   * The permissions the supplied role needs are in docs/iam.md.
   */
  readonly createIamRoles: boolean;
  /** ARN of a customer-managed Glue execution role. Used only when `createIamRoles` is false. */
  readonly glueRoleArn?: string;
  readonly accessRoleName?: string;
  readonly externalId?: string;
  readonly autoRunTransformAfterExtract: boolean;
}

export interface LoadConfig {
  readonly mode: LoadMode;
  readonly changedAfter: string | null;
  readonly dryRun: boolean;
  /**
   * Whether one failed record stops the whole load.
   *
   * `false` (default): a failed record is skipped and listed in the report, and every other
   * staged record still loads -- the common case, since one record's error (a transient target
   * throttle, a name collision, a malformed source payload) should not block the rest of a
   * registry. `true` stops the run (nonzero exit, report status FAILED) the moment any record
   * fails, for estates that want a load to be all-or-nothing.
   */
  readonly failOnRecordError: boolean;
  readonly allowReplayConfigurationDrift: boolean;
  readonly recordsPerObject: number;
  /**
   * How many records the load stage processes at once.
   *
   * Each record costs a target create followed by status polling, which is almost entirely waiting on
   * the network, so overlapping records with threads shortens a run roughly proportionally without
   * needing extra Glue capacity. 1 processes records one at a time.
   */
  readonly loadConcurrency: number;
  /**
   * Whether extraction also writes the readable per-record dump under
   * `reports/run_id=<id>/extracted-records/`.
   *
   * That dump is a second copy of the records already staged as JSONL under `runs/`, kept in a
   * form a person can open and diff. It is on by default because verifying a migration is the
   * point; turn it off for very large estates where a full second copy (kept for
   * `reportRetentionDays`, which is longer than `stagingRetentionDays`) is not worth the storage.
   * The old->new id crosswalk and the post-load record comparison are unaffected.
   */
  readonly dumpExtractedRecords: boolean;
  /**
   * Whether a migrated record is put into the status its Preview record holds.
   *
   * target creates every record in DRAFT, and a DRAFT record is not returned by data-plane search or the
   * browsing APIs, so an approved Preview record would arrive invisible. With this on (the default)
   * the load stage submits and, where needed, sets the status so the target record matches its source.
   * Turn it off to land everything in DRAFT for review inside the target registry before publishing.
   */
  readonly matchSourceStatus: boolean;
}

export interface TransformConfig {
  readonly namePrefix: string;
  readonly allowedRecordTypes: string[];
  readonly passthroughFields: string[];
}

export interface ApiConfig {
  readonly preview: Record<string, unknown>;
  readonly target: Record<string, unknown>;
}

export interface RuntimeConfig {
  readonly load: LoadConfig;
  readonly transform: TransformConfig;
}

export interface IamConfig {
  readonly previewReadActions: string[];
  readonly targetWriteActions: string[];
  readonly allowUnscopedRegistryResources: boolean;
}

export interface RegistryEndpointConfig {
  readonly accountId: string;
  readonly region: string;
  readonly registryId: string;
  readonly registryArn?: string;
  readonly roleArn?: string;
  readonly externalId?: string;
}

export interface RegistryMappingConfig {
  readonly id: string;
  readonly source: RegistryEndpointConfig;
  readonly target: RegistryEndpointConfig;
}

export interface MigrationConfig {
  readonly engine: EngineConfig;
  readonly runtime: RuntimeConfig;
  readonly iam: IamConfig;
  readonly registries: RegistryMappingConfig[];
}

export interface ResolvedRegistryEndpoint extends RegistryEndpointConfig {
  readonly roleArn?: string;
  readonly externalId?: string;
}

export interface ResolvedRegistryMapping {
  readonly id: string;
  readonly source: ResolvedRegistryEndpoint;
  readonly target: ResolvedRegistryEndpoint;
}

interface PartialMigrationConfig {
  readonly engine?: Partial<EngineConfig>;
  readonly runtime?: {
    readonly load?: Partial<LoadConfig>;
    readonly transform?: Partial<TransformConfig> & {
      // Backward-compatible alias for namePrefix from an earlier config revision.
      readonly identifierPrefix?: string;
    };
  };
  readonly iam?: Partial<IamConfig>;
  readonly registries?: RegistryMappingConfig[];
}

// Public Preview and the target registry control-plane settings. The HTTP contract itself comes from the service
// models the SDK supplies, so these only carry what the jobs cannot infer from a model: which
// endpoint to call, the field names the higher-level paging/matching logic reads, and how long to
// poll a write for.
//
// They live in a JSON file inside the Python package rather than in this file because BOTH sides
// need them: the stack publishes them to SSM for a deployed run, and the jobs read the same file
// directly when running locally with no deployment (`--local-dir`). One file means a local run and
// a Glue run cannot disagree about the API contract — which matters because that contract is part
// of the replay fingerprint, so any drift would show up as records that cannot be loaded.
//
// Two entries carry rationale worth keeping in view:
//   * `preview.response.recordTypePath` is `descriptorType` — the Preview field the target registry `recordType`
//     is inferred from.
//   * `target.poll.successStatuses` lists every settled state, not just the one a freshly created
//     record lands in. A record the customer has already submitted or approved is settled too, and
//     an incremental run at cutover has to be able to update it; treating APPROVED as unknown would
//     fail the run on exactly the records the customer had put into service.
const DEFAULT_PREVIEW_API: Record<string, unknown> = apiAdapterFile.preview;

function validatePreviewApiContract(api: Record<string, unknown>): void {
  if (api.transport !== 'sigv4RestJson') {
    throw new Error('runtime.api.preview.transport must be sigv4RestJson');
  }
  if (api.signingName !== 'bedrock-agentcore') {
    throw new Error('runtime.api.preview.signingName must be bedrock-agentcore');
  }
  if (api.endpointUrl !== null && api.endpointUrl !== undefined && api.endpointUrl !== '') {
    throw new Error('runtime.api.preview.endpointUrl overrides are not supported');
  }
  if (!Array.isArray(api.allowedEndpointHosts) || api.allowedEndpointHosts.length !== 0) {
    throw new Error('runtime.api.preview.allowedEndpointHosts must remain empty');
  }
  if (api.endpointUrlTemplate !== 'https://bedrock-agentcore-control.{region}.amazonaws.com') {
    throw new Error(
      'runtime.api.preview.endpointUrlTemplate must use the regional bedrock-agentcore-control amazonaws.com host',
    );
  }
}

const DEFAULT_TARGET_API: Record<string, unknown> = apiAdapterFile.target;

export function migrationApiAdapter(): ApiConfig {
  // A real deep copy. This was `deepMerge(DEFAULT_*_API, {})`, which with an empty override is just
  // `{...base}` -- a shallow clone whose nested objects (`request`, `response`, `poll`) stayed
  // shared with the imported JSON module across every call. Nothing mutates them today, so this is
  // pre-emptive: the function reads as though it hands out an independent copy, and one day
  // something will rely on that.
  const api: ApiConfig = {
    preview: structuredClone(DEFAULT_PREVIEW_API),
    target: structuredClone(DEFAULT_TARGET_API),
  };
  validateApiEndpoint(api.preview, 'migration adapter preview API');
  validateApiEndpoint(api.target, 'migration adapter target API');
  validatePreviewApiContract(api.preview);
  validateTargetApiContract(api.target);
  return api;
}

const DEFAULT_PREVIEW_READ_ACTIONS = [
  'bedrock-agentcore:ListRegistryRecords',
  'bedrock-agentcore:GetRegistryRecord',
  // Not used by the migration jobs, which only read records. `agent-registry-migration init` reads the
  // registry itself to derive the target registry configuration you have to re-apply, and for a
  // cross-account mapping it does that through this same assumed role -- so without this, the one
  // command that helps multi-account users fails with AccessDenied. Read-only, and scoped to the
  // registries the role can already read records from.
  'bedrock-agentcore:GetRegistry',
];

const DEFAULT_TARGET_WRITE_ACTIONS = [
  'agent-registry:ListRegistryRecords',
  'agent-registry:GetRegistryRecord',
  'agent-registry:CreateRegistryRecord',
  'agent-registry:UpdateRegistryRecord',
  // A migrated record is created in DRAFT, then moved to the status its Preview record holds
  // (runtime.load.matchSourceStatus). Without these two, an approved Preview record would land in the target registry
  // invisible to data-plane search and the browsing APIs.
  'agent-registry:SubmitRegistryRecordForApproval',
  'agent-registry:UpdateRegistryRecordStatus',
];

// Worker types a Glue 5.0 *batch* job accepts. G.025X is missing on purpose: it is streaming-only,
// and G.1X (4 vCPU / 16 GB) is therefore the smallest worker these jobs can run on. The old
// 0.0625 DPU Python shell worker was smaller still, which is why a companion check used to cap
// runtime.load.loadConcurrency at 4 on it -- 1 vCPU / 1 GB could not hold 32 record payloads and
// their in-flight requests at once. G.1X can, so that coupling is gone rather than relaxed.
const GLUE_WORKER_TYPES = ['G.1X', 'G.2X', 'G.4X', 'G.8X'];

/** Only used in messages; the version itself is fixed in migration-engine-stack.ts. */
const GLUE_VERSION_LABEL = '5.0';

/** Removed engine keys, and what to do instead. Present in a config file, each is an error. */
const REMOVED_ENGINE_KEYS: Record<string, string> = {
  glueMaxCapacity:
    'the jobs are Glue 5.0 Spark jobs now, which size by worker rather than by DPU: use ' +
    'engine.glueWorkerType (default G.1X) and engine.glueNumberOfWorkers (default 2). Glue rejects ' +
    'MaxCapacity together with a worker type, so this key cannot be honoured',
};

export function loadMigrationConfig(configPath: string): MigrationConfig {
  const absolutePath = path.resolve(configPath);
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`Migration configuration not found: ${absolutePath}`);
  }

  const parsed = JSON.parse(fs.readFileSync(absolutePath, 'utf8')) as PartialMigrationConfig;
  if (isRecord(parsed.runtime) && Object.prototype.hasOwnProperty.call(parsed.runtime, 'api')) {
    throw new Error(
      'runtime.api is managed by the migration solution and cannot be overridden in customer configuration',
    );
  }
  const engineInput = parsed.engine ?? {};
  // A key this solution no longer reads is worse than an invalid one: the file still parses, the
  // deploy still succeeds, and the operator believes they sized the job. Fail with the replacement.
  if (isRecord(parsed.engine)) {
    for (const [key, guidance] of Object.entries(REMOVED_ENGINE_KEYS)) {
      if (Object.prototype.hasOwnProperty.call(parsed.engine, key)) {
        throw new Error(`engine.${key} is no longer supported: ${guidance}`);
      }
    }
  }
  const deploymentId = engineInput.deploymentId ?? 'default';
  const engine: EngineConfig = {
    account: engineInput.account ?? process.env.CDK_DEFAULT_ACCOUNT,
    region: engineInput.region ?? process.env.CDK_DEFAULT_REGION,
    accessStackRegion: engineInput.accessStackRegion,
    partition: engineInput.partition ?? 'aws',
    stackName: engineInput.stackName ?? 'AgentRegistryMigrationEngine',
    deploymentId,
    parameterPrefix: normalizeParameterPrefix(
      engineInput.parameterPrefix ?? `/agent-registry-migration/${deploymentId}`,
    ),
    stagingRetentionDays: engineInput.stagingRetentionDays ?? 90,
    reportRetentionDays: engineInput.reportRetentionDays ?? 365,
    glueWorkerType: engineInput.glueWorkerType ?? 'G.1X',
    glueNumberOfWorkers: engineInput.glueNumberOfWorkers ?? 2,
    glueTimeoutMinutes: engineInput.glueTimeoutMinutes ?? 180,
    terminationProtection: engineInput.terminationProtection ?? true,
    glueRoleName: engineInput.glueRoleName,
    createIamRoles: engineInput.createIamRoles ?? true,
    glueRoleArn: engineInput.glueRoleArn,
    accessRoleName: engineInput.accessRoleName,
    externalId: engineInput.externalId,
    autoRunTransformAfterExtract: engineInput.autoRunTransformAfterExtract ?? false,
  };

  const loadInput = parsed.runtime?.load ?? {};
  const transformInput = parsed.runtime?.transform ?? {};
  const runtime: RuntimeConfig = {
    load: {
      mode: loadInput.mode ?? 'FULL',
      changedAfter: loadInput.changedAfter ?? null,
      dryRun: loadInput.dryRun ?? true,
      failOnRecordError: loadInput.failOnRecordError ?? false,
      allowReplayConfigurationDrift: loadInput.allowReplayConfigurationDrift ?? false,
      recordsPerObject: loadInput.recordsPerObject ?? 500,
      loadConcurrency: loadInput.loadConcurrency ?? 32,
      dumpExtractedRecords: loadInput.dumpExtractedRecords ?? true,
      matchSourceStatus: loadInput.matchSourceStatus ?? true,
    },
    transform: {
      namePrefix: transformInput.namePrefix ?? transformInput.identifierPrefix ?? 'migrated',
      allowedRecordTypes: transformInput.allowedRecordTypes ?? ['AGENT', 'MCP', 'SKILL', 'CUSTOM'],
      passthroughFields: transformInput.passthroughFields ?? ['description'],
    },
  };

  const iamInput = parsed.iam ?? {};
  const iam: IamConfig = {
    previewReadActions: iamInput.previewReadActions ?? DEFAULT_PREVIEW_READ_ACTIONS,
    targetWriteActions: iamInput.targetWriteActions ?? DEFAULT_TARGET_WRITE_ACTIONS,
    allowUnscopedRegistryResources: iamInput.allowUnscopedRegistryResources ?? false,
  };

  const config: MigrationConfig = {
    engine,
    runtime,
    iam,
    registries: parsed.registries ?? [],
  };
  validateConfig(config);
  return config;
}

export function engineGlueRoleName(config: MigrationConfig): string {
  return config.engine.glueRoleName ?? `AgentRegistryMigrationGlue-${iamNamePart(config.engine.deploymentId, 28)}`;
}

/**
 * The staging bucket's name: `<stack name>-<account>-<region>`, lowercased and sanitised to
 * what S3 accepts, rather than the random suffix CloudFormation generates for an unnamed bucket.
 *
 * Takes plain strings rather than a `MigrationConfig` so both the stack (which has one) and the
 * CLI's pre-deploy bucket-collision check (which only has the raw config file, before it is
 * parsed into a `MigrationConfig`) can compute the identical name without constructing one.
 *
 * `engine.account`/`engine.region` are required as soon as any registry is configured (see
 * `validateConfig`), so in the stack both are always concrete strings -- never a CDK token --
 * which is what makes a literal, human-readable name possible in the first place.
 *
 * S3 bucket names are globally unique, 3-63 characters, lowercase letters/digits/dots/hyphens
 * only, and *immutable* -- renaming one is always a replacement, a new bucket with the old one
 * left behind under its `RemovalPolicy.RETAIN`. Changing `stackName` on an already-deployed
 * engine has that effect, so it is not something to do casually on a live deployment.
 */
export function stagingBucketName(stackNameValue: string, account?: string, region?: string): string {
  const stack = s3NamePart(stackNameValue);
  // 63 total, minus the two "-" separators and the account/region lengths, is what is left for
  // the stack name segment. Account ids are a fixed 12 digits; regions are short and bounded, so
  // in practice this only ever trims a very long custom stackName.
  const suffix = [account, region].filter(Boolean).join('-');
  const budget = 63 - (suffix ? suffix.length + 1 : 0);
  const trimmed = stack.slice(0, Math.max(1, budget)).replace(/-+$/, '') || 'agent-registry-migration';
  return [trimmed, account, region].filter(Boolean).join('-');
}

/**
 * A Glue job's name: `<stack name>-<account>-<region>-<suffix>`, in place of the random suffix
 * CloudFormation generates for an unnamed job. Glue job names allow a much wider character set
 * and a 255-character limit, so -- unlike the bucket -- this needs no sanitising or truncation.
 *
 * Renaming a Glue job is also a CloudFormation replacement (a new job resource, the old one
 * deleted), which matters less than the bucket since a job carries no state of its own, but is
 * still worth knowing before changing `stackName` on a live deployment.
 */
export function glueJobName(
  stackNameValue: string,
  account: string | undefined,
  region: string | undefined,
  suffix: string,
): string {
  return [stackNameValue, account, region, suffix].filter(Boolean).join('-');
}

/** Sanitise a value to what S3 bucket names accept: lowercase letters, digits, dots, hyphens. */
function s3NamePart(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9.-]/g, '-')
    .replace(/^-+/, '');
}

export function generatedAccessRoleName(config: MigrationConfig): string {
  return config.engine.accessRoleName ?? `AgentRegistryMigrationAccess-${iamNamePart(config.engine.deploymentId, 26)}`;
}

export function migrationExternalId(config: MigrationConfig): string {
  return config.engine.externalId ?? `agent-registry-migration-${config.engine.deploymentId}`;
}

export function resolveRegistryMappings(config: MigrationConfig): ResolvedRegistryMapping[] {
  return config.registries.map((mapping) => ({
    id: mapping.id,
    source: resolveEndpoint(config, mapping.source),
    target: resolveEndpoint(config, mapping.target),
  }));
}

export function generatedAccessAccounts(config: MigrationConfig): string[] {
  const accounts = new Set<string>();
  for (const mapping of config.registries) {
    if (requiresGeneratedAccessRole(config, mapping.source)) {
      accounts.add(mapping.source.accountId);
    }
    if (requiresGeneratedAccessRole(config, mapping.target)) {
      accounts.add(mapping.target.accountId);
    }
  }
  return [...accounts].sort();
}

export function accessResourcesForAccount(
  config: MigrationConfig,
  accountId: string,
): { readonly source: string[]; readonly target: string[] } {
  const source = new Set<string>();
  const target = new Set<string>();
  for (const mapping of config.registries) {
    if (
      mapping.source.accountId === accountId &&
      requiresGeneratedAccessRole(config, mapping.source)
    ) {
      registryResourceArns(config, mapping.source, 'bedrock-agentcore').forEach((arn) => source.add(arn));
    }
    if (
      mapping.target.accountId === accountId &&
      requiresGeneratedAccessRole(config, mapping.target)
    ) {
      registryResourceArns(config, mapping.target, 'agent-registry').forEach((arn) => target.add(arn));
    }
  }
  return { source: [...source].sort(), target: [...target].sort() };
}

export function directAccessResources(
  config: MigrationConfig,
): { readonly source: string[]; readonly target: string[] } {
  const source = new Set<string>();
  const target = new Set<string>();
  for (const mapping of config.registries) {
    if (!mapping.source.roleArn && mapping.source.accountId === config.engine.account) {
      registryResourceArns(config, mapping.source, 'bedrock-agentcore').forEach((arn) => source.add(arn));
    }
    if (!mapping.target.roleArn && mapping.target.accountId === config.engine.account) {
      registryResourceArns(config, mapping.target, 'agent-registry').forEach((arn) => target.add(arn));
    }
  }
  return { source: [...source].sort(), target: [...target].sort() };
}

function registryResourceArns(
  config: MigrationConfig,
  endpoint: RegistryEndpointConfig,
  service: 'bedrock-agentcore' | 'agent-registry',
): string[] {
  const registryArn = endpoint.registryArn ??
    `arn:${config.engine.partition}:${service}:${endpoint.region}:${endpoint.accountId}:registry/${endpoint.registryId}`;
  return [registryArn, `${registryArn}/record/*`];
}

function resolveEndpoint(
  config: MigrationConfig,
  endpoint: RegistryEndpointConfig,
): ResolvedRegistryEndpoint {
  if (endpoint.roleArn || endpoint.accountId === config.engine.account) {
    return { ...endpoint };
  }
  return {
    ...endpoint,
    roleArn: `arn:${config.engine.partition}:iam::${endpoint.accountId}:role/${generatedAccessRoleName(config)}`,
    externalId: migrationExternalId(config),
  };
}

function requiresGeneratedAccessRole(
  config: MigrationConfig,
  endpoint: RegistryEndpointConfig,
): boolean {
  return !endpoint.roleArn && endpoint.accountId !== config.engine.account;
}

function validateConfig(config: MigrationConfig): void {
  if (!['FULL', 'INCREMENTAL'].includes(config.runtime.load.mode)) {
    throw new Error(`runtime.load.mode must be FULL or INCREMENTAL, got ${config.runtime.load.mode}`);
  }
  // INCREMENTAL may omit changedAfter: each mapping then resumes from the watermark saved by its
  // last successful load (the extract fails with guidance if no watermark exists yet).
  if (config.runtime.load.changedAfter !== null) {
    assertIso8601Timestamp(config.runtime.load.changedAfter, 'runtime.load.changedAfter');
  }
  for (const [field, value] of Object.entries({
    dryRun: config.runtime.load.dryRun,
    failOnRecordError: config.runtime.load.failOnRecordError,
    allowReplayConfigurationDrift: config.runtime.load.allowReplayConfigurationDrift,
    dumpExtractedRecords: config.runtime.load.dumpExtractedRecords,
    matchSourceStatus: config.runtime.load.matchSourceStatus,
  })) {
    if (typeof value !== 'boolean') {
      throw new Error(`runtime.load.${field} must be a boolean`);
    }
  }
  // Checked the same way loadConcurrency is, below. Without the Number.isInteger guard a fractional
  // 1.5 passed, and because `"500" < 1` and `"500" > 10000` are both false in JavaScript, so did a
  // string -- which then reached the job as a string and was coerced somewhere else.
  assertIntegerInRange(config.runtime.load.recordsPerObject, 'runtime.load.recordsPerObject', 1, 10_000);
  // Capped at 32: beyond that the target control plane, not the job, becomes the limit and throttling
  // costs more than the added parallelism gains.
  assertIntegerInRange(config.runtime.load.loadConcurrency, 'runtime.load.loadConcurrency', 1, 32);
  if (!GLUE_WORKER_TYPES.includes(config.engine.glueWorkerType)) {
    throw new Error(
      `engine.glueWorkerType ${JSON.stringify(config.engine.glueWorkerType)} is not a Glue ` +
        `${GLUE_VERSION_LABEL} batch worker type; use one of ${GLUE_WORKER_TYPES.join(', ')}`,
    );
  }
  // Glue's own floor for a Spark job. Asserted here so a 1 becomes a sentence at synth rather than a
  // CloudFormation ValidationException minutes into a deploy. There is no ceiling worth adding: the
  // jobs are single-threaded and use exactly one worker, so anything above the minimum only costs
  // money -- which is why the default is the minimum.
  assertIntegerInRange(config.engine.glueNumberOfWorkers, 'engine.glueNumberOfWorkers', 2, 299);
  // S3 lifecycle expiration is expressed in whole days, so these have to be integers -- and they
  // were previously only compared against 1, which a string or a fraction slips past.
  assertIntegerInRange(config.engine.stagingRetentionDays, 'engine.stagingRetentionDays', 1, 36_500);
  assertIntegerInRange(config.engine.reportRetentionDays, 'engine.reportRetentionDays', 1, 36_500);
  // Reports are the record of what a migration did; staged run data is the input it can be replayed
  // from. Expiring the report first leaves data nobody can interpret, and the lifecycle comment in
  // migration-engine-stack.ts already assumes this ordering.
  if (config.engine.reportRetentionDays < config.engine.stagingRetentionDays) {
    throw new Error(
      `engine.reportRetentionDays (${config.engine.reportRetentionDays}) must be at least ` +
        `engine.stagingRetentionDays (${config.engine.stagingRetentionDays}); reports outlive the ` +
        'staged data they describe',
    );
  }
  // Glue's own maximum for a job timeout is 7 days. Never validated at all before, so a typo became
  // a CloudFormation error at deploy time -- or, worse, a job that gave up part-way through a load.
  assertIntegerInRange(config.engine.glueTimeoutMinutes, 'engine.glueTimeoutMinutes', 1, 10_080);
  if (config.registries.length > 0 && (!config.engine.account || !config.engine.region)) {
    throw new Error('engine.account and engine.region are required when registry mappings are configured');
  }
  if (config.engine.autoRunTransformAfterExtract) {
    throw new Error(
      'engine.autoRunTransformAfterExtract must remain false; transform/load requires manual review approval',
    );
  }
  if (typeof config.engine.createIamRoles !== 'boolean') {
    throw new Error('engine.createIamRoles must be a boolean');
  }
  if (config.engine.createIamRoles && config.engine.glueRoleArn) {
    throw new Error(
      'engine.glueRoleArn is only valid when engine.createIamRoles is false; ' +
        'set createIamRoles to false to supply your own Glue execution role',
    );
  }
  if (!config.engine.createIamRoles) {
    if (config.engine.glueRoleArn && !/^arn:[^:]*:iam::\d{12}:role\/.+$/.test(config.engine.glueRoleArn)) {
      throw new Error('engine.glueRoleArn must be a valid IAM role ARN');
    }
    // With role creation disabled the solution cannot generate the cross-account access role
    // either, so every remote endpoint must name a customer-managed roleArn explicitly.
    const generated = generatedAccessAccounts(config);
    if (generated.length > 0) {
      throw new Error(
        'engine.createIamRoles is false, so cross-account access roles cannot be generated. ' +
          `Set an explicit roleArn on every endpoint in account(s): ${generated.join(', ')}`,
      );
    }
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9_.\/-]*$/.test(config.runtime.transform.namePrefix)) {
    throw new Error('runtime.transform.namePrefix must start with an alphanumeric character');
  }
  const validRecordTypes = new Set(['AGENT', 'MCP', 'SKILL', 'CUSTOM']);
  for (const recordType of config.runtime.transform.allowedRecordTypes) {
    if (!validRecordTypes.has(recordType)) {
      throw new Error(`runtime.transform.allowedRecordTypes contains unsupported record type ${recordType}`);
    }
  }
  const supportedPassthroughFields = new Set(['description']);
  for (const field of config.runtime.transform.passthroughFields) {
    if (!supportedPassthroughFields.has(field)) {
      throw new Error(
        `runtime.transform.passthroughFields contains field ${field}, which is not supported end-to-end`,
      );
    }
  }

  const mappingIds = new Set<string>();
  for (const mapping of config.registries) {
    if (!/^[A-Za-z0-9._-]+$/.test(mapping.id)) {
      throw new Error(`Registry mapping id contains unsupported characters: ${mapping.id}`);
    }
    if (mappingIds.has(mapping.id)) {
      throw new Error(`Duplicate registry mapping id: ${mapping.id}`);
    }
    mappingIds.add(mapping.id);
    validateEndpoint(mapping.id, 'source', mapping.source);
    validateEndpoint(mapping.id, 'target', mapping.target);
  }

  validateActions(config.iam.previewReadActions, 'iam.previewReadActions');
  validateActions(config.iam.targetWriteActions, 'iam.targetWriteActions');
}

/**
 * Require a whole number within an inclusive range.
 *
 * One helper because the numeric knobs were each checked slightly differently: some tested
 * `Number.isInteger`, some only compared against the bounds -- and a bare comparison lets a string
 * through, since `"500" < 1` and `"500" > 10000` are both false. Checking them all the same way is
 * what stops the next knob from being checked the loosest way.
 */
function assertIntegerInRange(value: unknown, field: string, min: number, max: number): void {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${field} must be an integer between ${min} and ${max}, got ${JSON.stringify(value)}`);
  }
}

/**
 * ISO-8601 as the migration jobs actually parse it.
 *
 * Deliberately not `Date.parse`, which this used to use. `Date.parse` accepts a long tail of
 * non-ISO formats -- `2026/08/01`, `August 1, 2026`, `Aug 1 2026 00:00:00 GMT` -- so a
 * configuration carrying one of those synthesised and deployed cleanly, and then the extract job
 * failed at startup on `datetime.fromisoformat`. The error message already promised ISO-8601; this
 * is the check that enforces it.
 *
 * Fractional seconds are restricted to exactly 3 or 6 digits because AWS Glue Python shell runs
 * Python 3.9, whose `datetime.fromisoformat` accepts only those two precisions. Python 3.11+
 * relaxed that, so a developer's newer interpreter would accept `.12` or `.123456789` while the
 * Glue worker rejects it -- validating against the runtime that matters is the point.
 */
const ISO_8601_TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{3}|\.\d{6})?)?(?:Z|[+-]\d{2}:\d{2})?)?$/;

function assertIso8601Timestamp(value: unknown, field: string): void {
  if (typeof value !== 'string' || !ISO_8601_TIMESTAMP.test(value.trim())) {
    throw new Error(
      `${field} must be an ISO-8601 timestamp (example 2026-08-01T00:00:00Z), got ${JSON.stringify(value)}. ` +
        'Accepted: YYYY-MM-DD, optionally followed by THH:MM[:SS[.fff|.ffffff]] and Z or +HH:MM.',
    );
  }
  // The shape is right; make sure it names a real instant, so 2026-02-31T00:00:00Z is refused here
  // rather than by the job.
  const parsed = new Date(value.trim().replace(' ', 'T'));
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`${field} is not a real date/time: ${JSON.stringify(value)}`);
  }
}

function validateEndpoint(mappingId: string, side: string, endpoint: RegistryEndpointConfig): void {
  if (!/^\d{12}$/.test(endpoint.accountId)) {
    throw new Error(`${mappingId}.${side}.accountId must be a 12-digit AWS account ID`);
  }
  if (!endpoint.region || !endpoint.registryId) {
    throw new Error(`${mappingId}.${side} requires region and registryId`);
  }
  if (endpoint.externalId && !endpoint.roleArn) {
    throw new Error(`${mappingId}.${side}.externalId is only valid with a customer-managed roleArn`);
  }
}

function validateActions(actions: string[], field: string): void {
  for (const action of actions) {
    if (!/^[A-Za-z0-9-]+:[A-Za-z0-9*]+$/.test(action) || action.startsWith('replace-')) {
      throw new Error(`${field} contains a placeholder or invalid IAM action: ${action}`);
    }
  }
}

function validateApiEndpoint(api: Record<string, unknown>, field: string): void {
  const endpoint = api.endpointUrl;
  if (endpoint === null || endpoint === undefined || endpoint === '') {
    return;
  }
  if (typeof endpoint !== 'string') {
    throw new Error(`${field}.endpointUrl must be a string or null`);
  }
  let parsed: URL;
  try {
    parsed = new URL(endpoint);
  } catch {
    throw new Error(`${field}.endpointUrl is not a valid URL`);
  }
  if (parsed.protocol !== 'https:') {
    throw new Error(`${field}.endpointUrl must use HTTPS`);
  }
  if (parsed.username || parsed.password) {
    throw new Error(`${field}.endpointUrl must not contain credentials`);
  }
  const allowedHosts = api.allowedEndpointHosts;
  if (!Array.isArray(allowedHosts) || !allowedHosts.every((host) => typeof host === 'string')) {
    throw new Error(`${field}.allowedEndpointHosts must be an array of hostnames`);
  }
  const normalizedHosts = allowedHosts.map((host) => host.toLowerCase());
  if (!normalizedHosts.includes(parsed.hostname.toLowerCase())) {
    throw new Error(
      `${field}.endpointUrl host ${parsed.hostname} must be explicitly listed in allowedEndpointHosts`,
    );
  }
}

function validateTargetApiContract(api: Record<string, unknown>): void {
  if (api.transport !== 'sigv4RestJson') {
    throw new Error('runtime.api.target.transport must be sigv4RestJson');
  }
  if (api.endpointUrl !== null && api.endpointUrl !== undefined && api.endpointUrl !== '') {
    throw new Error('runtime.api.target.endpointUrl overrides are not supported');
  }
  if (!Array.isArray(api.allowedEndpointHosts) || api.allowedEndpointHosts.length !== 0) {
    throw new Error('runtime.api.target.allowedEndpointHosts must remain empty');
  }
  if (api.signingName !== 'agent-registry') {
    throw new Error('runtime.api.target.signingName must be agent-registry');
  }
  if (api.endpointUrlTemplate !== 'https://agent-registry-control.{region}.api.aws') {
    throw new Error('runtime.api.target.endpointUrlTemplate must use the regional agent-registry-control api.aws host');
  }
}

function normalizeParameterPrefix(value: string): string {
  const prefixed = value.startsWith('/') ? value : `/${value}`;
  const normalized = prefixed.replace(/\/{2,}/g, '/').replace(/\/$/, '');
  if (!/^\/[A-Za-z0-9_.\/-]+$/.test(normalized)) {
    throw new Error(`Invalid SSM parameter prefix: ${value}`);
  }
  return normalized;
}

function iamNamePart(value: string, maxLength: number): string {
  const cleaned = value.replace(/[^A-Za-z0-9+=,.@_-]/g, '-');
  return cleaned.slice(0, maxLength) || 'default';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
