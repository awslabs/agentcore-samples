# Per-user data isolation for an agent on an S3 bucket mount

A [Strands](https://strandsagents.com) data analyst agent on Amazon Bedrock AgentCore Runtime, shared by
two rental agencies. Each agency asks questions about its own listings and about the market. The data is
Parquet in Amazon S3, which the agent queries with [DuckDB](https://duckdb.org) through two
**`s3BucketMount`** mounts.

Both agencies use the same runtime, the same execution role, and the same bucket, yet neither can reach
the other's files. **IAM enforces that with session context keys, not the agent's code.** Even if the
agent, a bug, or a prompt injection tried to read the other agency's data, AWS would deny the request.

For AgentCore Runtime concepts used here (sessions, platform versions, filesystem configurations,
session context keys), see the [AgentCore Runtime documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html).

```
 backend (invoke.py)                     AgentCore Runtime: one runtime, one execution role
 ┌───────────────────────┐              ┌─ session A (its own microVM) ─────────────────────────┐
 │ acme         -> sid A │─-invoke(A)──►│  agent ─ DuckDB ─► /mnt/data    ─► datasets/market/   │ shared, read-only
 │ brooklynhomes-> sid B │              │                 └► /mnt/private ─► users/A/           │ Acme only
 └───────────────────────┘              └───────────────────────────────────────────────────────┘
             │                          ┌─ session B (its own microVM) ─────────────────────────┐
             └──────────────invoke(B)──►│  agent ─ DuckDB ─► /mnt/data    ─► datasets/market/   │
                                        │                 └► /mnt/private ─► users/B/           │ Brooklyn Homes only
                                        └───────────────────────────────────────────────────────┘
   one IAM statement: users/${aws:AgentCoreRuntimeSourceSid}/*   -> resolves to A or B per request
```

## How the isolation works

Three pieces, each doing one job:

1. **The backend maps users to session IDs.** AgentCore doesn't know users; it isolates sessions. The
   application authenticates the user, looks up their session ID, and calls `InvokeAgentRuntime` with it.
   Users never pick their session ID. Here `infra/config.py` (`session_id()`) plays that role: each
   agency gets one random, durable ID, created on first use and kept only in the local state file.
2. **AgentCore stamps the session on its credentials.** Every session runs with the runtime's execution
   role, and AgentCore adds two IAM [session context keys](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-fuse-mounts.html#runtime-session-isolation-s3) to the credentials it vends to that
   session:
   `aws:AgentCoreRuntimeSourceSid` (the `runtimeSessionId`) and `aws:AgentCoreRuntimeSourceArn` (the
   runtime ARN). The caller can't set or change them. The `s3BucketMount` mounts use these credentials
   too, so the keys apply to every read and write through the mount.
3. **One policy for every user.** The execution role's policy (`infra/cfn.yaml`) uses the key as a
   variable, which IAM fills in per request with the calling session's ID:

   ```json
   {"Effect": "Allow", "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::<bucket>/users/${aws:AgentCoreRuntimeSourceSid}/*"}
   ```

   Writes are scoped the same way, to `users/${aws:AgentCoreRuntimeSourceSid}/outputs/*`. Adding a user
   changes no policy. A bucket policy adds denies on top, as defense in depth: a request that carries an
   AgentCore session ID can't read, write, or list anything outside its own `users/<id>/` prefix and the
   shared market data, whatever the identity policies say. For the mechanism and the session ID
   requirements, see [Scope each session to its own S3 prefix](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-fuse-mounts.html#runtime-session-isolation-s3).

The mounts mirror the same boundary in the file system:

| Mount | Prefix | Contents | Access |
|---|---|---|---|
| `/mnt/data` | `datasets/market/` | market data, the same for every agency | read-only (IAM grants `GetObject` only) |
| `/mnt/private` | `users/{runtimeSessionId}/` | `data/`: the agency's own listings; `outputs/`: what the agent saves | this session's prefix only: `data/` read-only, `outputs/` writable |

The `{runtimeSessionId}` placeholder makes each session's mount root its own prefix, so the other
agency's files don't even appear in the file system. IAM is what makes that boundary hold against direct
S3 API calls.

### Why the control is in IAM and not in the query

The agent runs SQL that the model writes. Row filters in that SQL, such as a `WHERE agency = ...` driven
by a mapping table, would be enforced by the agent's own code: anything that skipped the filter would
see everything the credentials can read. Context keys move the decision out of the agent. The trade-off
is that IAM controls objects and prefixes, not rows or columns, so **the data is laid out by who may see
it**. Each agency's private data, including columns the market data doesn't have (owners, commissions),
lives under its own prefix.

AWS Glue Data Catalog and AWS Lake Formation aren't needed for this. The catalog holds table metadata;
the data stays in S3 under the same policy. Lake Formation grants permissions to IAM principals, and
every session of a runtime runs as the same execution role.

## Layout

```
agent/
  app.py            BedrockAgentCoreApp + Strands agent + tools + the isolation probe
  Dockerfile        linux/arm64
  requirements.txt  pinned versions
infra/
  config.py         region, names, model, data size and seed, the agencies, user -> session mapping
  data_gen.py       generates the market data and each agency's portfolio into .cache/
  cfn.yaml          S3 bucket and bucket policy, ECR repository, execution role
  deploy.py         stack -> generated data into S3 -> image -> runtime
  invoke.py         the "backend": ask as an agency, or run the probe
  cleanup.py        deletes everything (dry run by default)
```

## Prerequisites

- An AWS account with access to Amazon Bedrock AgentCore and to the model in `infra/config.py`
  (`MODEL_ID`, Claude Opus 5.5 through its `us.` cross-region inference profile by default).
- Python 3.10+ and Docker with `buildx` (the image is built for `linux/arm64`).
- boto3 1.44.6 or later, the first release that knows `s3BucketMount` (installed by `requirements.txt`).
- AWS credentials in the environment for the account and region in `infra/config.py`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
source .venv/bin/activate
```

## Deploy

```bash
python infra/deploy.py
```

It runs four steps, each logged as `1/4` to `4/4`:

1. **Stack** (`cfn.yaml`): the bucket and its bucket policy, the ECR repository, and the execution role.
2. **Data:** runs `data_gen.py` for anything missing in `.cache/` (see [Sample data](#sample-data)),
   then uploads the market data to `datasets/market/` and each agency's portfolio to
   `users/<its session id>/data/`. This step stands in for the backend seeding a new user's prefix at
   sign-up. It runs with your credentials, which carry no session context key, so the bucket policy's
   per-session deny doesn't apply to it. A re-run uploads only files whose content changed (each
   object carries a SHA-256 in its metadata) and deletes objects the generator no longer produces,
   only under those prefixes.
3. **Image:** builds and pushes `agent/`. The tag is a hash of the directory, so an unchanged agent
   isn't rebuilt.
4. **Runtime:** creates or updates the runtime with platform version `V2` (required for
   `s3BucketMount` and for session context keys), public network mode, and the two mounts, then waits
   for `READY`.

Re-run `python infra/deploy.py` after any change. After changing only `agent/`,
`python infra/deploy.py --skip-stack` skips the stack and the data.

## See it work

The probe runs inside an agency's session and tries each object directly through the S3 API, with that
session's credentials:

```bash
python infra/invoke.py --agency acme --probe
```

```
caller:          arn:aws:sts::<account>:assumed-role/s3mount-isolation-AgentExecutionRole-…/AgentCore-MicroVM-…-DEFAULT
/mnt/private:    ['data']  (the mount root is this session's own prefix)
ALLOWED                  read  own portfolio      users/agency-acme-…/data/my_listings/portfolio.parquet
DENIED (403)             read  other's portfolio  users/agency-brooklynhomes-…/data/my_listings/portfolio.parquet
DENIED (AccessDenied)    write other's prefix     users/agency-brooklynhomes-…/outputs/probe.txt
ALLOWED                  read  market data        datasets/market/neighborhoods.csv
DENIED (AccessDenied)    write market data        datasets/market/probe.txt
```

`--agency brooklynhomes --probe` gives the mirror image. Reading its own portfolio succeeds only because
the session context key resolved to the session's ID; without the key, the policy variable wouldn't
match and that read would be denied too.

The probe creates its S3 client inside the invocation. A session's microVM is restored from a snapshot
taken before the session existed, and credentials loaded earlier, at import time, wouldn't carry the
session context key.

## Ask questions

```bash
python infra/invoke.py --agency acme \
  "How do my 2025 asking rents compare with the market in the neighborhoods where I have listings? Save a CSV and a chart." --download
```

Acme's answer finds its listings priced 3-14% above the market after adjusting for size and bedrooms,
as the generator intends. Asked as the other agency for Acme's commissions or owners, the agent answers
with Brooklyn Homes' own numbers and says it has no access to Acme's data:

```bash
python infra/invoke.py --agency brooklynhomes "Compare our median 2025 rent and commission with Acme Realty's."
```

Each agency always uses the same session, so the conversation continues across calls. `--reset` starts
a new conversation in the same session. `--download` copies the files the agent saved to
`./outputs/<agency>/`.

A first question takes about a minute end to end: starting the microVM, copying the market data to
local disk, and the model's own reasoning and tool calls, which is most of it. A follow-up in the same
session takes 15-30 s.

More questions:

- "Which of my listings have been on the market longer than the neighborhood median? Who are their owners?"
- "In which neighborhoods are we priced below the market for 1-bedrooms?"
- "How does the average commission vary by borough in my portfolio?"
- "Which Brooklyn neighborhood had the biggest increase in median rent from 2024 to 2025?"

## Adding a user

Add an entry to `AGENCIES` in `infra/config.py` and run `python infra/deploy.py`. The new agency gets a
session ID and a seeded portfolio under its own prefix. No policy, role, or mount changes: the same
`${aws:AgentCoreRuntimeSourceSid}` statement covers it.

In a real application, keep the user-to-session mapping in your backend (a table keyed by user, written
at sign-up), and never let a user choose their own session ID. See [Security](#security).

## Security

The isolation holds only if every piece around it does its part:

- **Session IDs are credentials.** IAM isolates sessions, not users: whoever invokes the runtime with a
  session ID gets that session's data. Generate session IDs randomly (this sample uses 48 hex
  characters from `secrets`), store them only in the backend, and never derive them from something
  guessable such as a user name or a hash of it. Use one opaque segment of 33 to 256 letters, digits,
  `_` or `-`, with no `/` or `.`, as the AgentCore documentation requires. The state file that holds
  this sample's IDs (`.state-<region>.json`) is in `.gitignore`.
- **Only the backend may invoke the runtime.** Grant `bedrock-agentcore:InvokeAgentRuntime` on the
  runtime's ARN to the backend's role only, never to end users. The same goes for
  `InvokeAgentRuntimeCommand`, which runs shell commands inside a session:

  ```json
  {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
   "Resource": "arn:aws:bedrock-agentcore:<region>:<account>:runtime/<runtime id>*"}
  ```
- **The probe is a demonstration.** The `probe` action exists to show the isolation and does nothing a
  session can't do anyway, but remove it from an agent you run for real users.
- **What the payload carries is the backend's word.** The agency's display name goes into the system
  prompt as given; pass only values your backend controls.
- **The model's output is untrusted input.** The SQL it writes runs in a DuckDB connection that can only
  read the data, and `invoke.py --download` only fetches files under the agency's own `outputs/` prefix,
  whatever S3 URIs the answer contains.
- **The bucket** blocks public access, enforces bucket-owner object ownership, encrypts at rest (SSE-S3),
  and denies requests that don't use TLS. The execution role trusts AgentCore only for runtimes in this
  account and region (`aws:SourceAccount`, `aws:SourceArn`).
- **The container runs as root** (the Dockerfile sets no `USER`). Root stays confined to the session's own
  microVM. This sample hasn't been tested as a non-root user; if you switch, check that the agent can
  still write to `/mnt/private/outputs`.

## Sample data

All data is **synthetic**: `infra/data_gen.py` generates it, and every value in it is made up. It
describes no real listing, person, agency, or market, so there are no data licensing terms to track. The
generator uses [Faker](https://github.com/joke2k/faker) (MIT license) for names, email addresses and
street addresses and NumPy for everything else, and writes Parquet with DuckDB. The same seed
(`DATA_GEN` in `infra/config.py`) always produces the same files; generating everything takes ~9 seconds.

**Market data** (`datasets/market/`, every agency reads it):

- `listings/listings_YYYY-MM.parquet`: one file per month, January 2024 to December 2025, one million
  listings each (24 million rows, ~340 MB).

  | Column | Meaning |
  |---|---|
  | `listing_id`, `listed_date` | listing identifier and the day it was posted |
  | `neighborhood_id`, `street_address` | where it is (join `neighborhood_id` to `neighborhoods`); the address is fictional |
  | `bedrooms` (0 = studio), `bathrooms`, `sqft` | size |
  | `rent_usd` | monthly asking rent, in steps of $25 |
  | `broker_fee`, `doorman`, `elevator`, `laundry_in_unit`, `pets_allowed` | yes/no features |
  | `year_built` | year the building was built |
  | `status`, `days_on_market`, `rented_date` | `rented`, `active` or `withdrawn`; how long it was listed; when it was rented |
  | `agent_name`, `agency` | listing agent and agency (fictional) |

- `neighborhoods.csv`: `neighborhood_id`, `neighborhood`, `borough` for 37 neighborhoods across the five
  boroughs. The names are real places; every number attached to them is invented.

**Each agency's portfolio** (`users/<session id>/data/my_listings/portfolio.parquet`, only that agency
reads it): the agency's own listings over the same months, with the same columns plus `owner_name`,
`owner_email`, and `commission_pct`.

| Agency (`--agency`) | Boroughs | Listings | Pricing vs. the market |
|---|---|---|---|
| Acme Realty (`acme`) | Manhattan, Brooklyn | 3,000 | ~6% above |
| Brooklyn Homes (`brooklynhomes`) | Brooklyn | ~2,000 | ~3% below |

The market data follows patterns so questions have answers worth finding: rent depends on neighborhood,
size, building age, and amenities; it peaks in July and August; it rises 2-9% a year depending on the
neighborhood (Bushwick, Bedford-Stuyvesant and Crown Heights fastest; Tribeca, SoHo and DUMBO slowest);
listings priced above the going rate stay on the market longer; broker fees are most common in Manhattan.

## Querying Parquet through the mount

On the first query of a session, the agent copies the data from the mounts to local disk and queries the
copy. Measured on the 2 vCPU microVM with the 24 million rows of market data:

| | Copy | `count(*)` | Median rent by neighborhood | Median rent and days on market by month |
|---|---|---|---|---|
| DuckDB reading the mount directly | – | 0.7 s | 18.9 s | 32.4 s |
| Copy to local disk, then query | 1.6-1.9 s, once per session | 0.01 s | 0.4 s | 0.3 s |

DuckDB reads Parquet with many small ranged reads, and through the mount each one is a round trip to S3.
A file copy is a large sequential read, which Mountpoint does fast: the agent copies 8 files at a time,
at ~190 MB/s. Data larger than `CACHE_MAX_BYTES` (4 GB; the microVM disk has ~8 GB free) is queried on
the mount in place instead.

Every directory of Parquet files and every CSV under `/mnt/data` and `/mnt/private/data` becomes a DuckDB
view (`listings`, `neighborhoods`, `my_listings`), so new data needs no code change.

## Things to know about `s3BucketMount`

- Each mount takes `bucket` and `mountPath`, and optionally `prefix`, `s3BucketOwner` (the account
  expected to own the bucket) and `s3BucketRegion`. `deploy.py` sets all of them.
- Requires runtime **platform version `V2`**.
- The `prefix` accepts one placeholder, `{runtimeSessionId}`.
- The mounts are set on the runtime (`filesystemConfigurations` in `CreateAgentRuntime` and
  `UpdateAgentRuntime`) and are the same for every session; only `{runtimeSessionId}` varies.
- The mounts use the session's credentials, session context keys included, so bucket and role policies
  apply to every file operation. A denied read or write shows up in the file system as an I/O error.
- It behaves like Mountpoint for Amazon S3 with default settings: fast sequential reads and ranged
  reads; new files written sequentially. No append, rename, truncate, or in-place edits, which also rules
  out SQLite databases and tools like `pip install` that rename files into place. Build such files on
  local disk and copy the result to the mount, as `save_csv` and `save_chart` do.
- Large sequential reads are fast; many small random reads are not. Copy data to local disk before
  reading it piece by piece, when it fits.

## Clean up

```bash
python infra/cleanup.py          # dry run: lists what it would delete
python infra/cleanup.py --yes    # deletes the runtime, empties the bucket, deletes the stack
```

If you deployed before changing `NAME` in `infra/config.py`, delete that deployment by its names:
`python infra/cleanup.py --stack-name <old stack> --runtime-name <old runtime> --yes`.

## Cost

You pay for AgentCore Runtime session time, Bedrock model tokens, S3 storage and requests, and ECR
storage for the image. There is no VPC or NAT gateway. Sessions stop after their idle timeout;
`cleanup.py` removes everything else.
