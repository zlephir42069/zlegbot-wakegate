"""Fire when an RSS or Atom feed gains an item id that has not been seen.

The first successful fetch records every current id and does not fire.
Seen ids are capped so the state file stays bounded; the oldest ids are dropped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from wakegate.checks.base import CheckResult
from wakegate.errors import CheckError
from wakegate.http import fetch

MAX_SEEN = 500
_SHOW = 5
_ID_LIMIT = 1024
_TITLE_LIMIT = 120


def evaluate(options: dict, state: dict, now: datetime) -> CheckResult:
    del now
    body = fetch(options["url"], options.get("timeout", 30))
    items = parse_feed(body)
    # Normal feeds list the newest entry first. Reverse on the baseline so
    # trimming drops the oldest ids when a single page is longer than the cap.
    if "seen" not in state:
        seen = _trim(list(reversed([item["id"] for item in items])))
        noun = "item" if len(items) == 1 else "items"
        return CheckResult(False, f"baseline, {len(items)} {noun}", {"seen": seen})

    previous = state.get("seen") or []
    if not isinstance(previous, list):
        previous = []
    seen_set = {item for item in previous if isinstance(item, str)}
    fresh = []
    for item in items:
        if item["id"] in seen_set:
            continue
        seen_set.add(item["id"])
        fresh.append(item)
    if not fresh:
        return CheckResult(False, "no new items", {"seen": [item for item in previous if isinstance(item, str)]})

    seen = _trim([item for item in previous if isinstance(item, str)] + [item["id"] for item in fresh])
    return CheckResult(True, _summary(fresh), {"seen": seen})


def parse_feed(data: bytes) -> list[dict]:
    cleaned = data.lstrip(b"\xef\xbb\xbf")
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        raise CheckError("feed XML could not be parsed") from exc
    root_name = _local(root.tag)
    if root_name == "feed":
        nodes = [child for child in list(root) if _local(child.tag) == "entry"]
        return [item for item in (_atom(node) for node in nodes) if item]
    nodes = [node for node in root.iter() if _local(node.tag) == "item"]
    return [item for item in (_rss(node) for node in nodes) if item]


def _rss(node: ET.Element) -> dict | None:
    ident = _child_text(node, "guid") or _child_text(node, "link")
    if not ident:
        return None
    title = _child_text(node, "title") or ident
    return {"id": ident[:_ID_LIMIT], "title": title[:_TITLE_LIMIT]}


def _atom(node: ET.Element) -> dict | None:
    ident = _child_text(node, "id") or _link_href(node)
    if not ident:
        return None
    title = _child_text(node, "title") or ident
    return {"id": ident[:_ID_LIMIT], "title": title[:_TITLE_LIMIT]}


def _link_href(node: ET.Element) -> str:
    fallback = ""
    for child in list(node):
        if _local(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        if not href:
            continue
        rel = (child.attrib.get("rel") or "alternate").lower()
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _local(child.tag) == name:
            return _text(child)
    return ""


def _text(node: ET.Element) -> str:
    value = "".join(node.itertext())
    return " ".join(value.split())


def _local(tag: str) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[-1].lower()
    return tag.lower()


def _trim(ids: list[str]) -> list[str]:
    """Keep the newest recorded ids. The list is oldest-first."""
    if len(ids) <= MAX_SEEN:
        return ids
    return ids[-MAX_SEEN:]


def _summary(items: list[dict]) -> str:
    titles = ", ".join(item["title"] for item in items[:_SHOW])
    extra = len(items) - _SHOW
    suffix = f" (+{extra} more)" if extra > 0 else ""
    noun = "item" if len(items) == 1 else "items"
    return f"{len(items)} new {noun}: {titles}{suffix}"
