"""Stable remote groups and button positions, independent of display names."""
from copy import deepcopy


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
