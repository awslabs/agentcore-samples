"""Ask the agent a question as one of the agencies, or check that the agencies are isolated.

    python infra/invoke.py --agency acme "How do my listings compare with the market in my neighborhoods?"
    python infra/invoke.py --agency acme "Chart it by neighborhood" --download
    python infra/invoke.py --agency acme --probe     # try to reach the other agency's data via the S3 API

This script plays the backend: it maps the agency to its session ID (config.session_id) and calls
InvokeAgentRuntime with it. In a real application, users never choose their session ID; the backend
does, after authenticating them.
"""

import argparse
import json

from config import AGENCIES, ROOT, client, die, find_runtime, load_state, log, session_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--agency", required=True, choices=list(AGENCIES))
    ap.add_argument("--reset", action="store_true", help="start a new conversation (same session, same private data)")
    ap.add_argument(
        "--download", action="store_true", help="also copy the files the agent saved into ./outputs/<agency>/"
    )
    ap.add_argument(
        "--probe", action="store_true", help="instead of a question, try the other agency's data through the S3 API"
    )
    a = ap.parse_args()
    if bool(a.prompt) == a.probe:
        ap.error("give a prompt or --probe")
    arn = (
        load_state().get("runtime_arn")
        or (find_runtime() or {}).get("agentRuntimeArn")
        or die("runtime not found -- run infra/deploy.py")
    )
    sid = session_id(a.agency)
    if a.probe:
        other = next(k for k in AGENCIES if k != a.agency)
        payload = {"action": "probe", "other_session_id": session_id(other)}
    else:
        payload = {"prompt": a.prompt, "agency": AGENCIES[a.agency]["name"], "reset": a.reset}
    log(f"{AGENCIES[a.agency]['name']} -> session {sid}")
    resp = client("bedrock-agentcore", read_timeout=900).invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=sid,
        qualifier="DEFAULT",
        payload=json.dumps(payload).encode(),
        contentType="application/json",
        accept="application/json",
    )
    out = json.loads(resp["response"].read())
    if "error" in out:
        die(out["error"])
    if a.probe:
        print(f"caller:          {out['caller']}")
        print(f"/mnt/private:    {out['mounted /mnt/private']}  (the mount root is this session's own prefix)")
        for what, result in out["s3 api"].items():
            print(f"{result:<24} {what}")
        return 0
    print(out["answer"])
    expected = f"s3://{load_state()['outputs']['DataBucketName']}/users/{sid}/outputs/"
    for uri in out["outputs"]:
        log(f"saved {uri}")
        if a.download:
            if not uri.startswith(expected) or "/" in uri[len(expected) :]:  # the agent's output is untrusted input
                log(f"  skipped: not under {expected}")
                continue
            bucket, key = uri.removeprefix("s3://").split("/", 1)
            dest = ROOT / "outputs" / a.agency / key.rsplit("/", 1)[1]
            dest.parent.mkdir(parents=True, exist_ok=True)
            client("s3").download_file(bucket, key, str(dest))
            log(f"  -> {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
