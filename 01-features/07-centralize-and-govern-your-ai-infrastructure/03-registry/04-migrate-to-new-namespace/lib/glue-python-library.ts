import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

export interface PythonLibraryBuildOptions {
  /** Package source root containing both pyproject.toml and the importable package directory. */
  readonly sourceDir: string;
  /** Directory the built wheel is written into. Created if missing. */
  readonly outputDir: string;
  /** Python interpreter to invoke. Defaults to the PYTHON env var or `python3`. */
  readonly python?: string;
}

/**
 * Builds the Glue common library into an importable wheel and returns its path.
 *
 * AWS Glue Python shell jobs can only import shared code from a `.egg` or `.whl`
 * provided through `--extra-py-files`; a plain module `.zip` is not a supported
 * import mechanism for Python shell jobs. The build is delegated to a
 * zero-dependency standard-library script so `cdk synth`/`cdk deploy` require only
 * `python3` on PATH -- no setuptools, wheel, build, or network access.
 */
export function buildPythonLibraryWheel(options: PythonLibraryBuildOptions): string {
  const builder = path.join(options.sourceDir, 'build_wheel.py');
  if (!fs.existsSync(builder)) {
    throw new Error(`Cannot build the Glue Python library: ${builder} is missing`);
  }

  fs.mkdirSync(options.outputDir, { recursive: true });
  for (const entry of fs.readdirSync(options.outputDir)) {
    if (entry.endsWith('.whl')) {
      fs.rmSync(path.join(options.outputDir, entry));
    }
  }

  const python = options.python ?? process.env.PYTHON ?? 'python3';
  try {
    execFileSync(python, [builder, '--source', options.sourceDir, '--outdir', options.outputDir], {
      stdio: 'pipe',
      // The wheel build is pure standard library and takes well under a second, so a couple of
      // minutes can only mean something is wedged -- better to say so than to hang `cdk synth`.
      timeout: 120_000,
      // execFileSync's default maxBuffer is 1 MB and it *throws ENOBUFS* when exceeded, which would
      // turn a chatty-but-successful build into a failure.
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
    });
  } catch (error) {
    const details = collectProcessOutput(error);
    throw new Error(
      `Failed to build the Glue Python library wheel using '${python}'. ` +
        'Ensure Python 3 is on PATH (override with the PYTHON environment variable).' +
        (details ? `\n${details}` : ''),
      // Keeps the underlying spawn error (ENOENT, ETIMEDOUT) reachable instead of flattening it
      // into the message.
      { cause: error },
    );
  }

  const wheels = fs.readdirSync(options.outputDir).filter((entry) => entry.endsWith('.whl'));
  const wheel = wheels[0];
  if (wheels.length !== 1 || !wheel) {
    throw new Error(
      `Expected exactly one wheel in ${options.outputDir} after building, found ${wheels.length}` +
        (wheels.length > 0 ? `: ${wheels.join(', ')}` : ''),
    );
  }
  return path.join(options.outputDir, wheel);
}

function collectProcessOutput(error: unknown): string {
  const candidate = error as { stdout?: Buffer | string; stderr?: Buffer | string };
  return [candidate?.stdout, candidate?.stderr]
    .map((value) => (value ? value.toString().trim() : ''))
    .filter(Boolean)
    .join('\n');
}
