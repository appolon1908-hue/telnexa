from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).parents[1]
PATCHER_PATH = ROOT / "docker/jasmin/harden_durability.py"


def load_patcher():
    spec = spec_from_file_location("telnexa_jasmin_durability", PATCHER_PATH)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jasmin_image_applies_restart_durability_patch():
    dockerfile = (ROOT / "docker/jasmin/Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "harden_durability.py" in dockerfile
    assert "python /tmp/telnexa-harden-jasmin-durability.py" in dockerfile
    assert "USER jasmin" in dockerfile
    assert "python:3.12-alpine@sha256:d09d15e60962" in dockerfile
    assert "0aac58e466d583d0f0436df7b8afa3dc96191263" in dockerfile
    assert (
        "--checksum=sha256:3024b111c93ecbf0b1873f0803cd00e8791354d2ac79bff65d071cb1361f574a"
        in dockerfile
    )
    assert "--require-hashes -r /build/requirements.lock" in dockerfile
    assert "apk upgrade" not in dockerfile
    assert "apk-tools=3.0.8-r0" in dockerfile
    assert "libcrypto3=3.5.8-r0" in dockerfile
    assert "sqlite-libs=3.53.4-r0" in dockerfile
    jasmin_service = compose.split("  jasmin:\n", 1)[1].split("\n  webhook-relay:", 1)[0]
    assert "read_only: true" in jasmin_service
    assert "/tmp:rw,noexec,nosuid,nodev,mode=1777,size=64m" in jasmin_service


def test_jasmin_entrypoint_uses_shell_available_in_minimal_alpine_runtime():
    entrypoint = (ROOT / "docker/jasmin/entrypoint.sh").read_text(encoding="utf-8")

    assert entrypoint.startswith("#!/bin/sh\nset -eu\n")
    assert "bash" not in entrypoint


def test_jasmin_patch_makes_topology_and_messages_durable(tmp_path):
    files = {
        "queues/factory.py": (
            "        return self.chan.queue_declare(*args, **keys).addCallback(self._queue_declared)\n"
            "        return self.chan.basic_publish(**args)\n"
        ),
        "managers/clients.py": "exchange_declare(exchange='messaging', type='topic')\n",
        "managers/dlr.py": "exchange_declare(exchange='messaging', type='topic')\n",
        "routing/router.py": (
            "exchange_declare(exchange='messaging', type='topic')\n"
            "exchange_declare(exchange='billing', type='topic')\n"
        ),
        "routing/throwers.py": (
            "exchange_declare(exchange=self.exchangeName,\n"
            "                                                    type='topic')\n"
        ),
    }
    for relative_path, source in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    load_patcher().apply(tmp_path)

    factory = (tmp_path / "queues/factory.py").read_text(encoding="utf-8")
    assert "keys.setdefault('durable', True)" in factory
    assert "content.properties['delivery-mode'] = 2" in factory
    for relative_path in (
        "managers/clients.py",
        "managers/dlr.py",
        "routing/router.py",
        "routing/throwers.py",
    ):
        assert "durable=True" in (tmp_path / relative_path).read_text(encoding="utf-8")
