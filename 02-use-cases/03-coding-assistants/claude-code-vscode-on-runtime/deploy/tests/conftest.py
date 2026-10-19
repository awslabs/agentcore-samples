import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent
sys.path.insert(0, str(HERE))

import fake_aws


def _load_devbox():
    spec = importlib.util.spec_from_file_location("devbox", DEPLOY / "devbox.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["devbox"] = mod
    spec.loader.exec_module(mod)
    return mod


devbox_module = _load_devbox()
CLIENT_ID = "0oaDEVBOXSPA1234567"
ADA, GRACE = "00uADA0000000000000A", "00uGRACE00000000000G"
PEOPLE = {  # who opens the workbench in the tests: uid, sign-in name, Okta groups (as the token's groups claim has them)
    "ada": (ADA, "ada.lovelace@example.com", ["devbox-users", "ai-claude-power"]),
    "grace": (GRACE, "grace.hopper@example.com", ["devbox-users", "ai-claude-standard"]),
}


def _load_provisioner():
    spec = importlib.util.spec_from_file_location("provisioner", DEPLOY / "provisioner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


provisioner_module = _load_provisioner()


@pytest.fixture
def devbox():
    devbox_module.Report.reset()
    return devbox_module


@pytest.fixture
def env():
    # The shipped example (devbox.env itself is git-ignored), with the two values a person fills in.
    e = devbox_module.parse_env_file((DEPLOY / "devbox.env.example").read_text())
    e["OKTA_DOMAIN"] = "example.okta.com"
    e["DEVBOX_OKTA_CLIENT_ID"] = CLIENT_ID
    return e


@pytest.fixture
def settings(devbox, env):
    s = devbox.load_settings(env)
    assert not s.errors, s.errors
    return s


@pytest.fixture
def world():
    return fake_aws.World()


@pytest.fixture
def sandbox(devbox, world, tmp_path, monkeypatch):
    """devbox.py wired to the fake AWS: no sleeping, a temp state file, stub image builds."""
    monkeypatch.setattr(devbox, "SLEEP", lambda s: None)
    monkeypatch.setattr(devbox, "ROLE_SETTLE_S", 0)
    monkeypatch.setattr(devbox, "provisioner_zip", lambda: b"PK\x03\x04 the provisioner's code")
    monkeypatch.setattr(devbox, "STATE_FILE", tmp_path / ".state.json")
    monkeypatch.setattr(devbox, "docker_ready", lambda: True)
    monkeypatch.setattr(devbox, "fetch_json", lambda url: {"issuer": url.rsplit("/.well-known", 1)[0]})
    images, prebuilds = {}, []
    for key, (repo, _, first) in devbox.IMAGES.items():
        d = tmp_path / key
        d.mkdir()
        (d / "Dockerfile").write_text(f"FROM scratch\nLABEL component={key}\n")
        images[key] = (repo, d, first)
    monkeypatch.setattr(devbox, "IMAGES", images)
    monkeypatch.setattr(devbox, "prebuild", lambda context_dir, cmd: prebuilds.append((context_dir.name, cmd)))
    builds = []

    def fake_build(context_dir, uri, registry, token):
        repo, tag = uri.rsplit("/", 1)[-1].split(":")
        assert registry == f"{fake_aws.ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com" and token
        world.repos[repo][tag] = 123_000_000
        builds.append(uri)

    monkeypatch.setattr(devbox, "build_and_push", fake_build)
    monkeypatch.setattr(devbox, "local_image_size", lambda uri: 1_500_000_000)  # no real `docker image inspect`
    return {"builds": builds, "tmp": tmp_path, "prebuilds": prebuilds}


def make_ctx(devbox, settings, world, check=False):
    return devbox.Ctx(
        s=settings, aws=devbox.Aws(settings, clients=world.clients()), state=devbox.load_state(), check=check
    )


def jwt_event(uid: str, login: str, groups: list[str], client_id: str = CLIENT_ID) -> dict:
    """What API Gateway hands the provisioner once its JWT authorizer has checked the token: the claims, with an
    array claim as a string ("[a b]")."""
    return {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "scopes": ["devbox"],
                    "claims": {
                        "cid": client_id,
                        "uid": uid,
                        "sub": login,
                        "groups": "[" + " ".join(groups) + "]",
                        "scp": "[openid devbox]",
                    },
                }
            }
        }
    }


@pytest.fixture
def visit(devbox, world):
    """visit(person or (uid, login, groups)): open the workbench as that person after a deploy. Runs the real
    provisioner.handler against the fake AWS, with the settings deploy gave the Lambda, asking again on every 202 as
    the page does. Returns (the last answer's status, its body, how many calls it took)."""
    import json as _json

    mod = provisioner_module

    def run(who, *, limit: int = 40, event: dict | None = None):
        uid, login, groups = PEOPLE[who] if isinstance(who, str) else who
        env = world.fns["devbox-provisioner"]["Environment"]["Variables"]
        mod.SETTINGS = devbox.load_settings(_json.loads(env["DEVBOX_SETTINGS"]))
        mod.PLAN = devbox.BoxPlan(**_json.loads(env["DEVBOX_PLAN"]))
        c = world.clients()
        mod._clients.clear()
        mod._clients.update(efs=c["efs"], iam=c["iam"], acc=c["bedrock-agentcore-control"], ddb=c["dynamodb"])
        for n in range(1, limit + 1):
            r = mod.handler(event or jwt_event(uid, login, groups), None)
            if r["statusCode"] != 202:
                return r["statusCode"], _json.loads(r["body"]), n
        return r["statusCode"], _json.loads(r["body"]), limit

    return run
