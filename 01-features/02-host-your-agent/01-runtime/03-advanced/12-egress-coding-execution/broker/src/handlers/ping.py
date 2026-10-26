import asyncio
import logging

from ..config import broker_config

logger = logging.getLogger(__name__)


async def handle_ping(params: dict) -> dict:
    domain = params.get("domain")
    count = params.get("count", 3)
    timeout = params.get("timeout", 5)

    if not domain:
        return {
            "status": "error",
            "error": {
                "code": "INVALID_PARAMS",
                "message": "Missing 'domain' parameter",
            },
        }

    if not broker_config.is_ping_domain_allowed(domain):
        return {
            "status": "error",
            "error": {
                "code": "POLICY_DENIED",
                "message": f"Domain not in ping allowlist: {domain}",
            },
        }

    cmd = ["ping", "-c", str(int(count)), "-t", str(int(timeout)), domain]
    logger.info("Executing: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=int(timeout) + 10)

        return {
            "status": "ok",
            "result": {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "domain": domain,
                "count": count,
                "timeout": timeout,
            },
        }
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "error": {"code": "PING_TIMEOUT", "message": f"Ping to {domain} timed out"},
        }
    except Exception as e:
        return {"status": "error", "error": {"code": "PING_FAILED", "message": str(e)}}
