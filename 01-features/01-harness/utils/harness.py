"""Harness lifecycle helpers shared by the samples in this folder."""

import time

# A newly created Harness has been observed taking ~150s to reach READY on the
# public network, ~255s in VPC mode (the service also has to attach ENIs in your
# subnets), and longer still when it is pulling a container image on top of that.
# The timeout below is deliberately generous: waiting a little longer is
# harmless, whereas giving up early leaves the sample invoking a harness that is
# not ready yet.
HARNESS_POLL_INTERVAL = 5
HARNESS_POLL_TIMEOUT = 600

# The full status enum is CREATING, CREATE_FAILED, UPDATING, UPDATE_FAILED,
# READY, DELETING, DELETE_FAILED — there is no plain "FAILED". Matching the
# real terminal states matters: a harness that fails to create (for example
# because the execution role is missing an s3files permission) reports
# CREATE_FAILED immediately, and treating that as "not ready yet" would poll a
# dead harness until the timeout and then hide the service's failureReason,
# which is the one piece of text that says how to fix it.
HARNESS_FAILURE_STATUSES = ("CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED")


def poll_harness_status(control, harness_id, target_status="READY", timeout=HARNESS_POLL_TIMEOUT):
    """Block until a Harness reaches `target_status`, and return its description.

    Raises RuntimeError if the harness lands in a failure state, or TimeoutError
    if `timeout` seconds elapse first. Both are deliberate: the caller is about
    to invoke this harness, so continuing with one that never became READY only
    turns a clear lifecycle error into a confusing invoke error later on.
    """
    deadline = time.monotonic() + timeout
    while True:
        resp = control.get_harness(harnessId=harness_id)
        status = resp["harness"]["status"]
        print(f"  Harness status: {status}")
        if status == target_status:
            return resp
        if status in HARNESS_FAILURE_STATUSES:
            reason = resp["harness"].get("failureReason", "")
            raise RuntimeError(f"Harness entered {status}. {reason}".strip())
        if time.monotonic() > deadline:
            raise TimeoutError(f"Harness not {target_status} after {timeout}s (current: {status})")
        time.sleep(HARNESS_POLL_INTERVAL)
