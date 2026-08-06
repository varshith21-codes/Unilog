"""What in the Python models is mechanically comparable to types.ts?"""

import enum
import inspect
import pkgutil
from importlib import import_module

from pydantic import BaseModel

import axiom

enums: dict[str, list[str]] = {}
models: dict[str, list[str]] = {}

for info in pkgutil.walk_packages(axiom.__path__, prefix="axiom."):
    try:
        module = import_module(info.name)
    except Exception:
        continue
    for name, obj in vars(module).items():
        if not inspect.isclass(obj) or obj.__module__ != info.name:
            continue
        if issubclass(obj, enum.Enum):
            enums.setdefault(name, [str(m.value) for m in obj])
        elif issubclass(obj, BaseModel) and obj is not BaseModel:
            models.setdefault(name, sorted(obj.model_json_schema().get("properties", {})))

print(f"ENUMS ({len(enums)}):")
for name, values in sorted(enums.items()):
    print(f"  {name:26} {values}")

print(f"\nPYDANTIC MODELS ({len(models)}):")
for name, fields in sorted(models.items()):
    print(f"  {name:26} {len(fields)} fields")
