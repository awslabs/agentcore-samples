"""Inspect the real CodeZip, not a fixture. No imports from the cross-built ZIP.

Usage: python3 tests/artifact_contract.py ZIP CODE_CONFIGURATION_JSON
Requires packaging (also a locked FastMCP transitive dependency). Linux native
execution still needs an authorized matching host; this is a packaging contract.
"""
import configparser
from email.parser import Parser
import json
from pathlib import Path
import re
import struct
import sys
import tomllib
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def check(zip_path, code):
    assert code["Runtime"] == "PYTHON_3_14", code
    assert code["EntryPoint"] == ["opentelemetry-instrument", "./main.py"], code
    lock = tomllib.loads((ROOT / "app/banking_assistant_tools/uv.lock").read_text())
    locked = {canonicalize_name(p["name"]): p["version"] for p in lock["package"]}
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert archive.read("main.py") == (ROOT / "app/banking_assistant_tools/main.py").read_bytes()
        assert archive.read("main.py").startswith(b"#!/usr/bin/env python3\n")
        assert archive.getinfo("main.py").external_attr >> 16 & 0o111
        launcher = archive.read("bin/" + code["EntryPoint"][0]).decode()
        assert launcher.startswith("#!/usr/bin/env python3\n"), launcher
        assert "from opentelemetry.instrumentation.auto_instrumentation import run" in launcher
        assert archive.getinfo("bin/opentelemetry-instrument").external_attr >> 16 & 0o111
        for module in ("opentelemetry/instrumentation/auto_instrumentation/__init__.py",
                       "opentelemetry/instrumentation/auto_instrumentation/sitecustomize.py",
                       "opentelemetry/sdk/trace/__init__.py", "botocore/auth.py",
                       "amazon/opentelemetry/distro/exporter/otlp/aws/common/aws_auth_session.py",
                       "fastmcp/__init__.py"):
            assert module in names, module
        distributions = {}
        for name in names:
            if name.endswith(".dist-info/METADATA"):
                metadata = Parser().parsestr(archive.read(name).decode())
                key = canonicalize_name(metadata["Name"])
                assert metadata["Version"] == locked[key], (key, metadata["Version"], locked[key])
                assert SpecifierSet(metadata.get("Requires-Python", "")).contains("3.14"), key
                distributions[key] = metadata
        assert "aws-opentelemetry-distro" in distributions
        assert "opentelemetry-instrumentation" in distributions
        assert "botocore" in distributions  # AWS OTLP SigV4/EMF needs the distro's patch extra.
        # Verify the transitive base dependency closure under the *target* marker
        # environment, not macOS. Optional extras are installed by the real lock.
        environment = {**default_environment(), "python_version": "3.14", "python_full_version": "3.14.0",
                       "sys_platform": "linux", "platform_system": "Linux", "platform_machine": "aarch64",
                       "implementation_name": "cpython", "implementation_version": "3.14.0", "extra": ""}
        for key, metadata in distributions.items():
            for text in metadata.get_all("Requires-Dist", []):
                requirement = Requirement(text)
                marker_env = {**environment, "extra": "patch" if key == "aws-opentelemetry-distro" else ""}
                if requirement.marker and not requirement.marker.evaluate(marker_env):
                    continue
                dependency = canonicalize_name(requirement.name)
                assert dependency in distributions, (key, dependency, "missing")
                assert requirement.specifier.contains(distributions[dependency]["Version"]), (key, text)
        # Ensure entry-point discovery actually selects the AWS distro/configurator.
        entrypoints = []
        for name in names:
            if name.endswith(".dist-info/entry_points.txt"):
                parser = configparser.ConfigParser()
                parser.read_string(archive.read(name).decode())
                for group in ("opentelemetry_distro", "opentelemetry_configurator"):
                    if parser.has_section(group):
                        for key, value in parser.items(group):
                            entrypoints.append((group, key, value))
                            module = value.split(":")[0].strip().replace(".", "/")
                            assert module + ".py" in names or module + "/__init__.py" in names, value
        assert any("aws" in value.lower() for group, key, value in entrypoints if group == "opentelemetry_distro")
        assert any("aws" in value.lower() for group, key, value in entrypoints if group == "opentelemetry_configurator")
        native = [name for name in names if name.endswith(".so")]
        assert native
        for name in native:
            data = archive.read(name)
            assert data[:4] == b"\x7fELF" and data[4:6] == b"\x02\x01", name
            assert struct.unpack("<H", data[18:20])[0] == 183, name  # ELF64 little-endian AArch64
            cpython = re.search(r"\.cpython-(\d+)", name)
            assert not cpython or cpython[1] == "314", name
        for name in names:
            if name.endswith(".dist-info/WHEEL"):
                wheel = Parser().parsestr(archive.read(name).decode())
                if wheel.get("Root-Is-Purelib") == "false":
                    tags = wheel.get_all("Tag", [])
                    compatible = []
                    for tag in tags:
                        py, abi, platform = tag.split("-", 2)
                        # AL2023 glibc is 2.34; accept only an explicit compatible ARM wheel.
                        if "aarch64" not in platform:
                            continue
                        floors = [int(n) for n in re.findall(r"manylinux_2_(\d+)", platform)]
                        if floors and min(floors) > 34:
                            continue
                        if "manylinux" not in platform:
                            continue
                        if (py == "cp314" and abi == "cp314") or (abi == "abi3" and py.startswith("cp") and int(py[2:]) <= 314):
                            compatible.append(tag)
                    assert compatible, (name, tags)
        print(f"PASS real CodeZip contract: {len(distributions)} locked distributions; {len(native)} ELF ARM64 libraries; launcher, AWS distro/configurator, Python 3.14 closure and wheel tags")
        print("NOT RUN: Linux ARM64 launcher/server execution or AWS telemetry delivery")


if __name__ == "__main__":
    check(Path(sys.argv[1]), json.loads(sys.argv[2]))
