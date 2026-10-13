"""Shape checks that narrow untrusted YAML and page JavaScript results.

They validate structure only; callers give the result meaning. The string
list and dict checks come from ``web_page.utils``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeIs

if TYPE_CHECKING:
    from web_page.types import RawYaml

    from generative_ai.site.types import RawJson


def is_str_keyed_mapping(value: RawYaml | RawJson) -> TypeIs[Mapping[str, object]]:
    return isinstance(value, Mapping) and all(isinstance(k, str) for k in value)


def is_list(value: RawYaml | RawJson) -> TypeIs[list[object]]:
    return isinstance(value, list)
