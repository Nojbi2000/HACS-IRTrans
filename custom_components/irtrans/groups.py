"""Stable remote groups and button positions, independent of display names."""
from copy import deepcopy
from uuid import uuid4
import re


def remote_groups(options):
    """Migrate the original positions without changing their code keys or IDs."""
    groups = options.get("remotes")
    if groups is None:
        return [{"id": "main", "name": "Remote 01", "base": 0,
                 "button_count": max(1, min(32, int(options.get("button_count", 8))))}]
    if not isinstance(groups, list) or not groups:
        raise ValueError("Invalid remote groups")
    ids, bases = set(), set()
    for group in groups:
        if (not isinstance(group, dict)
                or not isinstance(group.get("id"), str)
                or not isinstance(group.get("name"), str)
                or not 1 <= len(group["name"].strip()) <= 100
                or type(group.get("base")) is not int
                or not 0 <= group["base"] <= 1000000
                or group["base"] % 1000
                or type(group.get("button_count")) is not int
                or not 1 <= group["button_count"] <= 32
                or group["id"] in ids or group["base"] in bases
                or (group["base"] == 0) != (group["id"] == "main")):
            raise ValueError("Invalid remote group")
        ids.add(group["id"])
        bases.add(group["base"])
    if "main" not in ids:
        raise ValueError("Original remote group is missing")
    return deepcopy(groups)


def append_remote(options, name, button_count, remote_id=None):
    """Create stable group data shared by the panel and Options flow."""
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("Remote name must contain 1–100 characters")
    if type(button_count) is not int or not 1 <= button_count <= 32:
        raise ValueError("Number of buttons must be between 1 and 32")
    remote_id = uuid4().hex if remote_id is None else remote_id
    if not isinstance(remote_id, str) or re.fullmatch(r"[0-9a-f]{32}", remote_id) is None:
        raise ValueError("Invalid remote request ID")
    groups = remote_groups(options)
    for group in groups:
        if group["id"] == remote_id:
            if group["name"] != name or group["button_count"] != button_count:
                raise ValueError("This request was already saved with different settings")
            return groups, group
    base = max(group["base"] for group in groups) + 1000
    if base > 1000000:
        raise ValueError("No more remote groups can be added")
    group = {"id": remote_id, "name": name, "base": base, "button_count": button_count}
    groups.append(group)
    return groups, group
