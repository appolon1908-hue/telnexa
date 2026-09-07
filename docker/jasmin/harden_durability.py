#!/usr/bin/env python3
"""Make Jasmin's AMQP topology and published messages restart-durable."""

from importlib.util import find_spec
from pathlib import Path


def replace_exact(path: Path, old: str, new: str, count: int = 1) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old) != count:
        raise RuntimeError(f"unexpected Jasmin source layout: {path}")
    path.write_text(source.replace(old, new), encoding="utf-8")


def apply(package_root: Path) -> None:
    factory = package_root / "queues/factory.py"
    replace_exact(
        factory,
        "        return self.chan.queue_declare(*args, **keys).addCallback(self._queue_declared)",
        "        keys.setdefault('durable', True)\n"
        "        return self.chan.queue_declare(*args, **keys).addCallback(self._queue_declared)",
    )
    replace_exact(
        factory,
        "        return self.chan.basic_publish(**args)",
        "        content = args.get('content')\n"
        "        if content is None or not isinstance(content.properties, dict):\n"
        "            raise ValueError('persistent AMQP content is required')\n"
        "        content.properties['delivery-mode'] = 2\n"
        "        return self.chan.basic_publish(**args)",
    )

    single_line_exchanges = {
        "managers/clients.py": "exchange_declare(exchange='messaging', type='topic')",
        "managers/dlr.py": "exchange_declare(exchange='messaging', type='topic')",
        "routing/router.py": (
            "exchange_declare(exchange='messaging', type='topic')",
            "exchange_declare(exchange='billing', type='topic')",
        ),
    }
    for relative_path, declarations in single_line_exchanges.items():
        if isinstance(declarations, str):
            declarations = (declarations,)
        path = package_root / relative_path
        for declaration in declarations:
            replace_exact(path, declaration, declaration[:-1] + ", durable=True)")

    replace_exact(
        package_root / "routing/throwers.py",
        "exchange_declare(exchange=self.exchangeName,\n"
        "                                                    type='topic')",
        "exchange_declare(exchange=self.exchangeName,\n"
        "                                                    type='topic', durable=True)",
    )


def installed_package_root() -> Path:
    spec = find_spec("jasmin")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("installed Jasmin package not found")
    return Path(next(iter(spec.submodule_search_locations)))


if __name__ == "__main__":
    apply(installed_package_root())
