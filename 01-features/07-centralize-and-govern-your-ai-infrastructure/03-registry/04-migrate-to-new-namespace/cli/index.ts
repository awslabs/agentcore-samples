#!/usr/bin/env node
/**
 * `agent-registry-migration` -- migrate AWS Agent Registry records to the new version of Registry.
 *
 * Nine commands, one configuration file, and one flag that decides whether anything is written.
 *
 * The command table below is the single description of the surface: it drives the help you get for
 * the tool, the help you get for one command, and which flags each command accepts. They cannot
 * disagree with each other, and a flag aimed at the wrong command is refused rather than dropped.
 */
import { CliError } from './context';
import * as commands from './commands';

type Options = commands.Options;

interface FlagSpec {
  /** Value placeholder, for a flag that takes one. */
  readonly argument?: string;
  readonly help: string;
}

interface CommandSpec {
  /** One line, for the command list. */
  readonly summary: string;
  /** What this command does and when to reach for it, for `<command> --help`. */
  readonly detail: string;
  readonly flags: readonly string[];
  /** Worked examples for `<command> --help`. */
  readonly examples?: readonly string[];
}

/** Accepted by every command, so no command has to list them. */
const GLOBAL_FLAGS: Record<string, FlagSpec> = {
  '--config': {
    argument: '<path>',
    help: 'Configuration file (default: ./migration.config.json, then ./config/migration.json)',
  },
};

const FLAGS: Record<string, FlagSpec> = {
  '--live': { help: 'Create the records in the target registries. Off by default' },
  '--dry-run': { help: 'Transform and report without writing. The default; say it to be sure' },
  '--resume': {
    argument: '[run-id]',
    help: 'Load an extract you already reviewed. With no id, the most recent one',
  },
  '--incremental': { help: 'Only records changed since this pair last loaded successfully' },
  '--since': {
    argument: '<when>',
    help: 'Incremental from an explicit ISO-8601 cutoff (implies --incremental)',
  },
  '--glue': { help: 'Use the deployed Glue engine instead of running here' },
  '--local': { help: 'Stage in a local directory, even if a bucket is deployed' },
  '--run-id': { argument: '<id>', help: 'A specific run (default: the most recent)' },
  '--json': { help: 'Machine-readable output' },
  '--offline': { help: 'Configuration only, make no AWS calls' },
  '--yes': { help: 'Proceed without the confirmation prompt' },
  '--delete-data': { help: 'Also delete the staging bucket and everything in it' },
  '--keep-reports': { help: 'With --delete-data, keep the reports' },
  '--force': { help: 'Overwrite an existing configuration' },
  '--create': { help: 'Create each target registry from the derived configuration, and wait for it' },
};

const COMMANDS: Record<string, CommandSpec> = {
  init: {
    summary: 'Ask for your registries once, and write the configuration file.',
    detail:
      'Asks for each preview registry and the target registry to migrate it into, and writes the\n' +
      'configuration every other command reads. Your account and region come from your\n' +
      'credentials, and each side of a pair can be in a different account or region.\n' +
      'When you have no target registry yet, init derives its configuration from the preview one,\n' +
      'gives you the single command that creates it, and takes the resulting id back into the file.',
    flags: ['--force'],
  },
  check: {
    summary: 'Validate the configuration, assume every role, and call both registries.',
    detail:
      'Reads the configuration, assumes any roles it names, and calls each registry, so an access\n' +
      'or configuration problem surfaces in seconds instead of part-way through a migration.\n' +
      'Pass the same flags you intend to pass to run: check reports the run you are about to do,\n' +
      'so --live makes it warn that records WILL be written, and --incremental checks the\n' +
      'watermarks that run would use.',
    flags: [
      '--live',
      '--dry-run',
      '--incremental',
      '--since',
      '--glue',
      '--local',
      '--json',
      '--offline',
    ],
    examples: [
      'check            # validate the configuration, assume every role, call both registries',
      'check --live     # the same, reported for a run that writes, so it warns that it will',
      'check --offline  # configuration only: uses no credentials and makes no AWS calls',
      'check --json     # the same result as data, for a pipeline gate',
    ],
  },
  run: {
    summary: 'The whole migration in one command. Writes nothing unless --live.',
    detail:
      'Validate, read the preview registries, transform every record to the target shape, load, and\n' +
      'report -- in one command. Without --live nothing is written: you get the transformed payloads\n' +
      'and the full report to review first, and can then load exactly those records.\n\n' +
      'Prefer `extract` and `load` when reading and writing are separate decisions, taken at\n' +
      'different times or by different people. Both routes do the same work and leave the same files.\n\n' +
      'Records keep their name, content and approval status; they get new record ids, and every run\n' +
      'writes the old -> new crosswalk.',
    flags: ['--live', '--dry-run', '--resume', '--incremental', '--since', '--glue', '--local'],
    examples: [
      'run --dry-run                # read, transform and report every record. Calls no target write API',
      'run --live                   # the same, and create the target records as it goes',
      'run --live --resume          # skip the read: load the extract you last reviewed, byte for byte',
      'run --live --resume <run-id> # the same, for an extract you name rather than the latest',
      'run --incremental --live     # only records changed since this pair last loaded successfully',
      'run --live --glue            # run both stages on the deployed engine, not on this machine',
    ],
  },
  extract: {
    summary: 'Read the preview registries into staging, and print the run id. Never writes to the target registry.',
    detail:
      'The first half of a migration on its own: validate, then read every record into staging and\n' +
      'report what was read. Nothing is written to the target registry, and nothing can be -- this command has no\n' +
      '--live.\n\n' +
      'It prints the run id, which is what `load` and `report` take. `load` defaults to the most\n' +
      'recent extract, so in the common case you do not have to pass it anywhere.',
    flags: ['--incremental', '--since', '--glue', '--local'],
    examples: [
      'extract                # read every record of every configured registry into staging',
      'extract --incremental  # read only what changed since this pair last loaded successfully',
      'extract --glue         # read on the deployed Glue engine, staging in its bucket',
    ],
  },
  load: {
    summary: 'Transform and load an extract, the most recent by default. Writes nothing unless --live.',
    detail:
      'The second half: transform every staged record to the target shape and load it, then report.\n' +
      'Defaults to the most recent extract -- normally the one you just reviewed -- and --run-id\n' +
      'names any other.\n\n' +
      'Loading the same extract twice is safe: a record already in the target is recognised by name\n' +
      'and updated or left alone, never duplicated.',
    flags: ['--live', '--dry-run', '--run-id', '--glue', '--local'],
    examples: [
      'load --dry-run            # transform the most recent extract and report it, writing nothing',
      'load --live               # create the target records from that same extract',
      'load --live --run-id <id> # create them from the extract you name instead of the latest',
    ],
  },
  report: {
    summary: 'Show what a run did, where its files are, and write its report page.',
    detail:
      'Prints the outcome of a run -- created, updated, unchanged and failed counts, the approval\n' +
      'status of the migrated records, and the location of every artifact it produced, including\n' +
      'the old -> new id crosswalk. Defaults to the most recent run.',
    flags: ['--run-id', '--glue', '--local', '--json'],
  },
  deploy: {
    summary: 'Deploy the Glue engine, for large estates or unattended runs (optional).',
    detail:
      'Deploys the migration engine: two Glue jobs, a staging bucket and the configuration\n' +
      'parameters, via CDK. Optional -- every command works without it, running here and staging\n' +
      'in a local directory. Deploy when a run is long enough to want it off your laptop, or when\n' +
      'the staged data and reports should be shared with your team. The bucket it creates is\n' +
      'recorded in your configuration, so nothing has to be copied out of the stack outputs.',
    flags: ['--yes'],
  },
  destroy: {
    summary: 'Remove the Glue engine. Never deletes migrated records.',
    detail:
      'Deletes the engine stack. Migrated target records are never touched, whatever you pass.\n' +
      'Without --yes it only prints what would go and what would survive. The staging bucket is\n' +
      'kept unless you add --delete-data, because it holds your reports and crosswalks.',
    flags: ['--yes', '--delete-data', '--keep-reports'],
  },
  'target-config': {
    summary: "Derive a target registry's configuration from its Preview registry.",
    detail:
      'Reads a preview registry and writes the settings the matching target registry should be created\n' +
      'with, translated to the target shape. Reads only, so the payload can be reviewed -- its\n' +
      'discovery configuration decides who may read the registry. Add --create to create each\n' +
      'registry from that payload, wait for it to become READY, and record the generated id in your\n' +
      'configuration. init offers the same thing during setup.',
    flags: ['--create'],
  },
};

const EXAMPLES = `A first migration, one step at a time:
  init                 asks for your registries once, and writes migration.config.json
  check                assumes every role and calls both registries. Writes nothing  (optional)
  extract              reads every preview record into staging, and prints the run id
  load --dry-run       transforms that extract to the new schema and reports it       (optional)
  load --live          creates the target records from exactly those staged bytes

Or the whole thing in one command:
  run --dry-run        read, transform and report. Calls no target write API
  run --live           the same, creating the target records as it goes

Afterwards:
  report               what a run did, and where its files are
  run --incremental --live    at cutover: only what changed since the last load
`;

const CLOSING = `Records keep the name, content and approval status they have in preview. They get new record
ids, and the old -> new crosswalk is in every run's report.

Run "agent-registry-migration <command> --help" for one command.
`;

/** A command line that could never work, whatever the state of the account. */
function usageError(message: string): CliError {
  return new CliError(message, 2);
}

function flagLabel(flag: string, spec: FlagSpec): string {
  return spec.argument ? `${flag} ${spec.argument}` : flag;
}

/** Which commands accept a flag, in the order they appear in the command list. */
function commandsAccepting(flag: string): string[] {
  return Object.keys(COMMANDS).filter((name) => COMMANDS[name]!.flags.includes(flag));
}

function renderFlagLines(entries: [string, FlagSpec][], annotate: boolean): string[] {
  const labels = entries.map(([flag, spec]) => flagLabel(flag, spec));
  const width = Math.max(...labels.map((label) => label.length));
  return entries.map(([flag, spec], index) => {
    const where = annotate && !(flag in GLOBAL_FLAGS) ? `${commandsAccepting(flag).join('/')}: ` : '';
    return `  ${labels[index]!.padEnd(width)}  ${where}${spec.help}`;
  });
}

function renderGlobalHelp(): string {
  const commandList = Object.entries(COMMANDS)
    .map(([name, spec]) => `  ${name.padEnd(14)} ${spec.summary}`)
    .join('\n');
  const options = renderFlagLines(
    [...Object.entries(GLOBAL_FLAGS), ...Object.entries(FLAGS)],
    true,
  ).join('\n');
  return `agent-registry-migration -- move Agent Registry records to the new version of Registry

Usage: agent-registry-migration <command> [options]

Commands:
${commandList}

Options:
${options}
  -h, --help      Show this, or the help for one command
  -v, --version   Show the version

${EXAMPLES}
${CLOSING}`;
}

function renderCommandHelp(name: string): string {
  const spec = COMMANDS[name]!;
  const flags = spec.flags.map((flag) => `[${flagLabel(flag, FLAGS[flag]!)}]`).join(' ');
  const options = renderFlagLines(
    [
      ...spec.flags.map((flag) => [flag, FLAGS[flag]!] as [string, FlagSpec],),
      ...Object.entries(GLOBAL_FLAGS),
    ],
    false,
  ).join('\n');
  const examples = spec.examples
    ? `\nExamples:\n${spec.examples.map((line) => `  agent-registry-migration ${line}`).join('\n')}\n`
    : '';
  return `agent-registry-migration ${name} -- ${spec.summary}

Usage: agent-registry-migration ${name}${flags ? ` ${flags}` : ''} [--config <path>]

${spec.detail}

Options:
${options}
${examples}`;
}

interface Parsed {
  readonly command?: string;
  readonly options: Options;
}

const VALUE_FLAGS = new Set(['--config', '--resume', '--run-id', '--since']);
/**
 * Flags whose value may be left off.
 *
 * `--resume` with an id loads that extract; bare, it loads the most recent one, which is what
 * someone who just reviewed a dry run means -- the id was on screen a moment ago and copying it
 * from a log adds a step and a way to get it wrong.
 */
const OPTIONAL_VALUE_FLAGS = new Set(['--resume']);

function parse(argv: string[]): Parsed {
  let command: string | undefined;
  const values: Record<string, string> = {};
  const flags = new Set<string>();

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]!;
    if (!argument.startsWith('-')) {
      if (!command) {
        command = argument;
        continue;
      }
      throw usageError(`Unexpected argument "${argument}". Run with --help.`);
    }
    const [name, inlineValue] = splitFlag(argument);
    if (VALUE_FLAGS.has(name)) {
      if (OPTIONAL_VALUE_FLAGS.has(name) && inlineValue === undefined) {
        // Only consume the next argument when it is a value rather than the next flag.
        const next = argv[index + 1];
        if (next === undefined || next.startsWith('-')) {
          flags.add(name);
          continue;
        }
        values[name] = next;
        index += 1;
        continue;
      }
      const value = inlineValue ?? argv[++index];
      if (!value) {
        throw usageError(`${name} needs a value.`);
      }
      values[name] = value;
      continue;
    }
    if (inlineValue !== undefined) {
      throw usageError(`${name} does not take a value.`);
    }
    flags.add(name);
  }

  const used = [...flags, ...Object.keys(values)];
  const known = new Set([...Object.keys(GLOBAL_FLAGS), ...Object.keys(FLAGS)]);
  for (const flag of used) {
    if (!known.has(flag)) {
      throw usageError(`Unknown option "${flag}". Run with --help.`);
    }
  }
  assertFlagsBelongToCommand(command, used);

  // These name two different places to run and stage, so accepting both would mean silently
  // honouring one of them.
  if (flags.has('--glue') && flags.has('--local')) {
    throw usageError(
      '--glue runs on the deployed engine and stages in its bucket; --local runs here and stages ' +
        'in a directory. Pass one.',
    );
  }
  // The one decision that matters most, asked for both ways at once.
  if (flags.has('--live') && flags.has('--dry-run')) {
    throw usageError(
      '--live creates the target records; --dry-run writes nothing. Pass one -- and note that ' +
        'dry run is what you get with neither.',
    );
  }
  // --keep-reports narrows --delete-data. On its own it reads as an instruction to keep something
  // that was never going to be deleted, so it is refused here rather than accepted and ignored --
  // the same reason a flag on the wrong command is refused.
  if (flags.has('--keep-reports') && !flags.has('--delete-data')) {
    throw usageError(
      '--keep-reports only means something with --delete-data, which is what would otherwise ' +
        'delete them. Without --delete-data the staging bucket and every report in it are kept ' +
        'already.',
    );
  }

  return {
    command,
    options: {
      config: values['--config'],
      resume: values['--resume'],
      runId: values['--run-id'],
      since: values['--since'],
      live: flags.has('--live'),
      dryRun: flags.has('--dry-run'),
      // `--resume` with no id: load the most recent extract, resolved when the run starts.
      resumeLatest: flags.has('--resume'),
      // --since names a cutoff, which only means anything for an incremental run, so it implies one
      // rather than failing on a combination the user clearly meant.
      incremental: flags.has('--incremental') || Boolean(values['--since']),
      glue: flags.has('--glue'),
      // Deploying records the staging bucket in the configuration, after which a run stages there by
      // default -- right when a deployment exists, since the run then shares its data and reports
      // with the team. --local overrides that per run without editing the file, so "no AWS
      // infrastructure" stays a choice you can make at any time rather than one you have to
      // un-deploy for.
      local: flags.has('--local'),
      json: flags.has('--json'),
      offline: flags.has('--offline'),
      yes: flags.has('--yes'),
      deleteData: flags.has('--delete-data'),
      keepReports: flags.has('--keep-reports'),
      force: flags.has('--force'),
      create: flags.has('--create'),
    },
  };
}

/**
 * Refuse a flag this command does not act on.
 *
 * Checking flags only against one global list means `check --live` or `init --json` is accepted and
 * then quietly discarded, which reads as though the tool agreed to something it never did. Every
 * flag is a per-run decision, so a flag on the wrong command means the run is not the one the
 * person asked for.
 */
function assertFlagsBelongToCommand(command: string | undefined, used: string[]): void {
  if (!command || !(command in COMMANDS)) {
    // An unknown command is reported on its own, with the list of real ones.
    return;
  }
  const allowed = new Set([...COMMANDS[command]!.flags, ...Object.keys(GLOBAL_FLAGS)]);
  for (const flag of used) {
    if (allowed.has(flag)) {
      continue;
    }
    const accepted = commandsAccepting(flag);
    const applies = accepted.length
      ? ` It applies to ${accepted.join(', ')}.`
      : '';
    throw usageError(
      `${command} does not take ${flag}.${applies} ` +
        `Run "agent-registry-migration ${command} --help".`,
    );
  }
}

function splitFlag(argument: string): [string, string | undefined] {
  const marker = argument.indexOf('=');
  return marker === -1
    ? [argument, undefined]
    : [argument.slice(0, marker), argument.slice(marker + 1)];
}

function version(): string {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const pkg = require('../../package.json') as { version?: string };
  return pkg.version ?? '0.0.0';
}

/**
 * Help for the command being asked about, when one is.
 *
 * `run --help` asking about `run` is the whole point, and `help run` is what people type when the
 * flag form does not occur to them, so both routes end in the same place.
 */
function helpFor(argv: string[]): string | undefined {
  const positional = argv.filter((argument) => !argument.startsWith('-'));
  const asked = positional[0] === 'help' ? positional[1] : positional[0];
  if (asked && asked in COMMANDS) {
    return renderCommandHelp(asked);
  }
  if (asked && positional[0] === 'help') {
    throw usageError(
      `No command named "${asked}". Expected ${Object.keys(COMMANDS).join(', ')}.`,
    );
  }
  return undefined;
}

async function main(argv: string[]): Promise<number> {
  if (argv.length === 0) {
    // No command is a usage error, not a successful no-op: a script that runs the tool with an
    // empty argument must not see success. The help still prints, on stderr where it belongs.
    process.stderr.write(renderGlobalHelp());
    process.stderr.write('\nNo command given.\n');
    return 2;
  }
  if (argv.includes('--help') || argv.includes('-h') || argv[0] === 'help') {
    process.stdout.write(helpFor(argv) ?? renderGlobalHelp());
    return 0;
  }
  if (argv.includes('--version') || argv.includes('-v')) {
    process.stdout.write(`${version()}\n`);
    return 0;
  }

  const { command, options } = parse(argv);
  switch (command) {
    case 'init':
      return commands.init(options);
    case 'check':
      return commands.check(options);
    case 'run':
      return commands.run(options);
    case 'extract':
      return commands.extract(options);
    case 'load':
      return commands.load(options);
    case 'report':
      return commands.report(options);
    case 'deploy':
      return commands.deploy(options);
    case 'destroy':
      return commands.destroy(options);
    case 'target-config':
      return commands.targetConfig(options);
    default:
      throw usageError(
        `Unknown command "${command ?? ''}". Expected ${Object.keys(COMMANDS).join(', ')}.`,
      );
  }
}

/**
 * Set the exit code and let Node exit on its own, rather than calling `process.exit`.
 *
 * `process.exit` terminates immediately, discarding anything still buffered on stdout -- which for a
 * piped or redirected stream (`report --json > run.json`) can be the tail of the output. Assigning
 * `exitCode` keeps the code and lets the runtime flush first.
 */
function finish(code: number): void {
  process.exitCode = code;
}

main(process.argv.slice(2))
  .then(finish)
  .catch((error: unknown) => {
    if (error instanceof CliError) {
      process.stderr.write(`${error.message}\n`);
      finish(error.exitCode);
      return;
    }
    process.stderr.write(`${(error as Error).stack ?? String(error)}\n`);
    finish(1);
  });
