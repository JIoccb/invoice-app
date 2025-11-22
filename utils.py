from __future__ import annotations

import math
from typing import Optional

import bleach
from flask import request


def sanitize_text(text_value: Optional[str]) -> Optional[str]:
    if text_value is None:
        return None
    return bleach.clean(text_value, tags=[], strip=True)


def get_pagination_params(default_per_page: int = 10) -> tuple[int, int]:
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", default_per_page))
    except ValueError:
        per_page = default_per_page
    page = max(page, 1)
    per_page = max(min(per_page, 100), 1)
    return page, per_page


def calc_pages(total: int, per_page: int) -> int:
    if not per_page:
        return 1
    return math.ceil(total / per_page)
