"""Shape checks that narrow untrusted YAML and page JavaScript results.

They validate structure only; callers give the result meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeIs

if TYPE_CHECKING:
    from generative_ai.googleflow.types import RawJson, RawYaml


def is_str_list(value: RawYaml | RawJson) -> TypeIs[list[str]]:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def is_str_dict(value: RawYaml | RawJson) -> TypeIs[dict[str, str]]:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def is_str_keyed_mapping(value: RawYaml | RawJson) -> TypeIs[Mapping[str, object]]:
    return isinstance(value, Mapping) and all(isinstance(k, str) for k in value)


def is_list(value: RawYaml | RawJson) -> TypeIs[list[object]]:
    return isinstance(value, list)
