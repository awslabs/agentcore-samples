# 01 — Custom Containers

Run a harness agent inside **your own container image** instead of the default Amazon Linux VM. This unlocks any runtime, system library, or pre-installed dependency your agent needs.

## Why custom containers?

By default, a harness session runs on Amazon Linux 2023 with Python pre-installed. But many real-world agents need:

- Specific language runtimes (Node.js, Go, Rust, Java, Ruby...)
- System libraries (ImageMagick, FFmpeg, headless Chromium...)
- Pre-installed dependencies (frameworks, ML models, your own source code)
- A locked-down environment that matches production

Custom containers let you bring your own Linux ARM64 image — public ECR, private ECR, or any OCI-compliant registry.

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`01_custom_container_node.ipynb`](01_custom_container_node.ipynb) | Notebook | Attaches a **Node.js** container, asks the agent to build an HTTP server, installs `chalk` via npm, runs it — all inside the agent's VM. |
| [`02_custom_container_cli.py`](02_custom_container_cli.py) | CLI script | Standalone command-line version — works with **any container image** via `--language node\|go\|python` presets or a raw `--container URI`. |
| [`03_custom_container_go.ipynb`](03_custom_container_go.ipynb) | Notebook | Attaches a **Go** container, has the agent write + `go build` + run an HTTP server, then **cross-compiles** the binary for linux/amd64. |

## Key concepts demonstrated

- **`environmentArtifact.optionalValue.containerConfiguration.containerUri`** — the field on `update_harness` that attaches a container image
- **`systemPrompt`** — telling the agent what runtime it has available so it picks the right tools
- **`invoke_agent_runtime_command`** (ExecuteCommand) — imperative commands on the VM, bypassing the agent loop (useful to verify `node --version`, `go env`, inspect generated files)
- **Session persistence** — same `runtimeSessionId` keeps the VM state across invocations (files and installed packages stick around)

## How to run

### Notebook
Open in Jupyter/VSCode and run cells in order. Cleanup cells at the bottom delete the harness.

### CLI
```bash
# Language presets
python 02_custom_container_cli.py --language node      # default
python 02_custom_container_cli.py --language go
python 02_custom_container_cli.py --language python

# Any ARM64-compatible container image
python 02_custom_container_cli.py \
    --container public.ecr.aws/docker/library/rust:slim \
    --message "Write a Rust program that prints system info."

# Other options
python 02_custom_container_cli.py --skip-cleanup   # keep harness after demo
python 02_custom_container_cli.py --raw-events     # dump raw streaming JSON
python 02_custom_container_cli.py --help
```

## Sample container images

| Image | Use case |
|---|---|
| `public.ecr.aws/docker/library/node:slim` | Node.js / npm ecosystem |
| `public.ecr.aws/docker/library/golang:1.24` | Go toolchain + cross-compilation |
| `public.ecr.aws/docker/library/python:3.12-slim` | Python with specific version |
| Your private ECR image | Custom dependencies, pre-loaded source code, ML models |

> Containers must support **linux/arm64**. The harness VM runs on ARM.
