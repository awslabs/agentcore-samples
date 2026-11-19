"""Data analyst agent for rental agencies: answers questions about an agency's own listings and the
shared market data, by querying Parquet files in S3 with DuckDB through AgentCore s3BucketMount mounts.

    /mnt/data     s3://<bucket>/datasets/market/         shared market data, read-only, same for everyone
    /mnt/private  s3://<bucket>/users/<runtimeSessionId>/  this session's user only: data/ (its own files)
                                                         and outputs/ (what the agent saves)

Which user a session belongs to is decided outside the agent: the backend maps each user to a session
ID. IAM enforces the isolation with the session context key aws:AgentCoreRuntimeSourceSid, so nothing
in this file has to be trusted to keep one agency out of another's data.

Payloads:
    {"prompt": "...", "agency": "Acme Realty"}   ask a question ("reset": true starts a new conversation)
    {"action": "probe", "other_session_id": "..."} try to reach another user's data directly through the
                                                   S3 API, to show IAM denying it (demo only)
"""

import concurrent.futures
import csv
import os
import re
import shutil
import threading
import time

import boto3
import duckdb
import matplotlib
from botocore.exceptions import ClientError

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402

DATA = "/mnt/data"
PRIVATE = "/mnt/private"
PRIVATE_DATA = f"{PRIVATE}/data"
OUTPUTS = f"{PRIVATE}/outputs"
SCRATCH = "/tmp/analyst"
CACHE = f"{SCRATCH}/cache"
CACHE_MAX_BYTES = 4 * 1024**3  # the microVM disk has ~8 GB free; bigger datasets are queried in place
BUCKET = os.environ["DATA_BUCKET"]
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-opus-5-5")
MAX_ROWS = 50

app = BedrockAgentCoreApp()
_session = {"id": None, "agency": None, "written": []}
_lock = threading.Lock()


def _cache(src_root: str, dst_root: str) -> str:
    """Copy src_root into local disk once per session and return the directory to query.

    Measured on a 2 vCPU microVM with the sample's 24M rows (~340 MB of Parquet): DuckDB querying the
    mount directly took 19-32 s per aggregation, because it issues many small ranged reads and each
    one pays a round trip to S3. Copying the files is a sequential read, which Mountpoint does fast:
    ~2 s for the whole dataset, after which the same queries took 0.3-0.4 s. Datasets too big for
    local disk are queried on the mount as they are."""
    files = [os.path.join(d, f) for d, _, fs in os.walk(src_root) for f in fs]
    if sum(os.path.getsize(f) for f in files) > CACHE_MAX_BYTES:
        return src_root

    def copy(src: str) -> None:
        dst = dst_root + src[len(src_root):]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)

    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(8) as ex:  # one large sequential read per file, in parallel
        list(ex.map(copy, files))
    print(f"[analyst] copied {len(files)} files ({sum(map(os.path.getsize, files)) / 1e6:,.0f} MB) from {src_root} "
          f"to local disk in {time.monotonic() - t0:.1f} s", flush=True)
    return dst_root


def _connect() -> duckdb.DuckDBPyConnection:
    """SQL comes from the model, so the connection can only read the data and spill to local /tmp. It
    can't write anywhere (only the save_* tools write to /mnt/private/outputs, see _publish), change its
    settings, or install extensions. It also can't reach another agency's data, but that isn't this
    code's doing: those files aren't mounted, and IAM denies them to this session."""
    roots = {DATA: _cache(DATA, f"{CACHE}/market")}
    if os.path.isdir(PRIVATE_DATA):
        roots[PRIVATE_DATA] = _cache(PRIVATE_DATA, f"{CACHE}/private")
    os.makedirs(f"{SCRATCH}/duckdb", exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{SCRATCH}/duckdb'")
    con.execute(f"SET allowed_directories=['{DATA}/', '{PRIVATE_DATA}/', '{CACHE}/', '{SCRATCH}/duckdb/']")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    for mount, root in roots.items():
        for name, source in _discover(root).items():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM {source}")
            VIEWS[name] = source.replace(f"'{root}", f"'{mount}")  # describe them by where the data lives
    return con


VIEWS: dict[str, str] = {}


def _discover(root: str) -> dict[str, str]:
    """One view per table found under root, so new data needs no code change: a directory of Parquet
    files is one table (e.g. listings/ with a file per month), and every CSV file is a table of its own."""
    views = {}
    for d, _, files in sorted(os.walk(root)):
        found = []
        if any(f.endswith(".parquet") for f in files):
            found.append((os.path.basename(d), f"read_parquet('{d}/*.parquet')"))
        found += [(f[:-4], f"read_csv_auto('{d}/{f}')") for f in sorted(files) if f.endswith(".csv")]
        for name, source in found:
            name = re.sub(r"\W+", "_", name).strip("_").lower() or "data"
            while name in views or name in VIEWS:
                name += "_"
            views[name] = source
    return views


_con = None
_con_lock = threading.Lock()


def con() -> duckdb.DuckDBPyConnection:
    global _con
    with _con_lock:
        if _con is None:
            _con = _connect()
    return _con.cursor()


def _markdown(cols: list[str], rows: list[tuple]) -> str:
    cell = lambda v: f"{v:,.2f}" if isinstance(v, float) else str(v)  # noqa: E731
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def _publish(local: str, filename: str) -> str:
    """Copy a finished file from local scratch into the mount, in one sequential write.

    Mountpoint can't append, rename or rewrite part of a file, so nothing is ever built in place on
    /mnt/private/outputs: files are written locally, then copied once. Existing names are never
    reused, since overwriting may also be disallowed. The object is in S3 as soon as close() returns."""
    os.makedirs(OUTPUTS, exist_ok=True)
    stem, ext = os.path.splitext(re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-.") or "result")
    name, n = f"{stem}{ext}", 1
    while os.path.exists(f"{OUTPUTS}/{name}"):
        n += 1
        name = f"{stem}-{n}{ext}"
    shutil.copyfile(local, f"{OUTPUTS}/{name}")
    uri = f"s3://{BUCKET}/users/{_session['id']}/outputs/{name}"
    _session["written"].append(uri)
    return uri


@tool
def list_datasets() -> str:
    """List the data files this session can read, with their sizes: the shared market data under
    /mnt/data and your agency's own files under /mnt/private/data."""
    out = []
    for root in (DATA, PRIVATE_DATA):
        for d, _, files in os.walk(root):
            out += [f"{os.path.join(d, f)}  {os.path.getsize(os.path.join(d, f)) / 1e6:,.1f} MB" for f in sorted(files)]
    return "\n".join(out[:200]) or "no files"


@tool
def run_sql(query: str) -> str:
    """Run a read-only DuckDB SQL query and return up to 50 rows as a markdown table.

    Use the views listed in your instructions; they read a local copy of the data when it fits on
    disk. Any other file under /mnt/data or /mnt/private/data can be read with read_parquet /
    read_csv_auto.

    Args:
        query: a DuckDB SELECT (or DESCRIBE / SUMMARIZE) statement.
    """
    t0 = time.monotonic()
    try:
        rel = con().sql(query)
        if rel is None:
            return "the statement returned no rows; run_sql is for SELECT, DESCRIBE and SUMMARIZE"
        cols, rows = rel.columns, rel.limit(MAX_ROWS + 1).fetchall()
    except duckdb.Error as e:
        return f"SQL error: {e}"
    more = f"\n(showing the first {MAX_ROWS} rows)" if len(rows) > MAX_ROWS else ""
    return f"{_markdown(cols, rows[:MAX_ROWS])}{more}\n({(time.monotonic() - t0) * 1000:,.0f} ms)"


@tool
def save_csv(query: str, filename: str) -> str:
    """Run a DuckDB query and save its full result as a CSV in your agency's outputs.

    Args:
        query: a DuckDB SELECT statement.
        filename: file name for the result, e.g. "rent-vs-market.csv".
    """
    try:
        rel = con().sql(query)
        cols, rows = rel.columns, rel.fetchall()
    except duckdb.Error as e:
        return f"SQL error: {e}"
    local = f"{SCRATCH}/{time.time_ns()}.csv"
    with open(local, "w", newline="") as f:  # written here, not by DuckDB: SQL has no write access anywhere
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return f"saved {_publish(local, filename if filename.endswith('.csv') else filename + '.csv')}"


@tool
def save_chart(query: str, kind: str, title: str, filename: str) -> str:
    """Plot a query result and save it as a PNG in your agency's outputs.

    Args:
        query: a DuckDB SELECT returning the x values in the first column and one or more numeric series after it.
        kind: "bar" or "line".
        title: the chart title.
        filename: file name for the chart, e.g. "rent-vs-market.png".
    """
    try:
        rel = con().sql(query)
        cols, rows = rel.columns, rel.fetchall()
    except duckdb.Error as e:
        return f"SQL error: {e}"
    if len(cols) < 2 or not rows:
        return "the query must return an x column and at least one numeric column, and at least one row"
    x = [str(r[0]) for r in rows]
    horizontal = kind == "bar" and len(cols) == 2 and max(map(len, x)) > 8  # long category names
    fig, ax = plt.subplots(figsize=(10, max(5, 0.4 * len(x)) if horizontal else 5))
    for i, col in enumerate(cols[1:], start=1):
        ys = [r[i] for r in rows]
        if horizontal:
            ax.barh(x, ys, label=col)
        elif kind == "bar" and len(cols) == 2:
            ax.bar(x, ys, label=col)
        else:
            ax.plot(x, ys, marker="o", label=col)
    ax.set_title(title)
    if horizontal:
        ax.invert_yaxis()  # first row on top
        ax.set_xlabel(cols[1])
    else:
        ax.set_xlabel(cols[0])
        ax.set_ylabel(cols[1] if len(cols) == 2 else "")
        if len(x) > 12:
            ax.tick_params(axis="x", labelrotation=60)
    if len(cols) > 2:
        ax.legend()
    fig.tight_layout()
    local = f"{SCRATCH}/{time.time_ns()}.png"
    fig.savefig(local, dpi=120)
    plt.close(fig)
    return f"saved {_publish(local, filename if filename.endswith('.png') else filename + '.png')}"


SYSTEM_PROMPT = """You are a data analyst for {agency}, a New York City rental agency. You answer questions by
querying data with DuckDB through your tools. The files live in Amazon S3 and are mounted as directories:
{data} holds market data shared by every agency, and {private} holds {agency}'s own files.

DuckDB views, one per table:
{views}

- Views whose source is under {private} hold {agency}'s own data, including private columns (owners,
  commissions). Everything else is market data.
- You only have {agency}'s data. If asked about another agency's own listings, owners or results, say you
  don't have access to them; the market data only shows other agencies' public listings.
- Start with a DESCRIBE when you don't know a view's columns yet; list_datasets shows the raw files.
- Push work into SQL (aggregations, filters, LIMIT): run_sql returns at most {max_rows} rows.
- Use save_csv for result tables and save_chart for charts the user can open later; mention each saved
  file's S3 URI in your answer.
- Base every number in your answer on a query you ran. Say so when the data can't answer the question.
"""

_agent = None


def agent(agency: str) -> Agent:
    global _agent
    if _agent is None:
        con()  # finds the tables (and copies the data locally) so the prompt can name the views
        _agent = Agent(
            model=BedrockModel(model_id=MODEL_ID, region_name=os.environ["AWS_REGION"], max_tokens=16000),
            tools=[list_datasets, run_sql, save_csv, save_chart],
            system_prompt=SYSTEM_PROMPT.format(
                agency=agency, data=DATA, private=PRIVATE, max_rows=MAX_ROWS,
                views="\n".join(f"- {n}: {src}" for n, src in VIEWS.items()) or "- none"),
            callback_handler=None,
        )
    return _agent


def probe(own: str, other: str) -> dict:
    """Try each object directly with the S3 API, with this session's credentials. The client is created
    here, inside the invocation: the microVM is restored from a snapshot taken before the session
    existed, and credentials loaded at import time wouldn't carry the session context key."""
    s3 = boto3.Session().client("s3", region_name=os.environ["AWS_REGION"])

    def attempt(op: str, key: str) -> str:
        try:
            if op == "read":
                s3.head_object(Bucket=BUCKET, Key=key)
            else:
                s3.put_object(Bucket=BUCKET, Key=key, Body=b"probe")
            return "ALLOWED"
        except ClientError as e:
            return f"DENIED ({e.response['Error']['Code']})"

    portfolio = "data/my_listings/portfolio.parquet"
    market_key = next((f"datasets/market/{os.path.relpath(os.path.join(d, f), DATA)}"
                       for d, _, fs in os.walk(DATA) for f in fs), "datasets/market/")
    return {
        "caller": boto3.Session().client("sts", region_name=os.environ["AWS_REGION"]).get_caller_identity()["Arn"],
        "mounted /mnt/private": sorted(os.listdir(PRIVATE)),
        "s3 api": {
            f"read  own portfolio      users/{own}/{portfolio}": attempt("read", f"users/{own}/{portfolio}"),
            f"read  other's portfolio  users/{other}/{portfolio}": attempt("read", f"users/{other}/{portfolio}"),
            f"write other's prefix     users/{other}/outputs/probe.txt": attempt("write", f"users/{other}/outputs/probe.txt"),
            f"read  market data        {market_key}": attempt("read", market_key),
            "write market data        datasets/market/probe.txt": attempt("write", "datasets/market/probe.txt"),
        },
    }


@app.entrypoint
def invoke(payload, context):
    global _agent
    payload = payload or {}
    with _lock:  # one session = one microVM = one conversation; invocations take turns
        _session["id"], _session["written"] = context.session_id, []
        if payload.get("action") == "probe":
            return probe(context.session_id, payload.get("other_session_id", "another-session"))
        prompt = payload.get("prompt")
        if not prompt:
            return {"error": "payload must include 'prompt'"}
        if payload.get("reset"):
            _agent = None
        os.makedirs(SCRATCH, exist_ok=True)
        answer = str(agent(payload.get("agency") or "the agency")(prompt))
        return {"answer": answer, "outputs": list(_session["written"]), "session_id": context.session_id}


if __name__ == "__main__":
    app.run()
