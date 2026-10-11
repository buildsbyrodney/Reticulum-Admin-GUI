"""Stable, defensive models for Reticulum utility JSON.

The rnstatus CLI is the source of truth, but its JSON field names and optional
values are CLI-version/interface-specific. Templates consume only these models,
never raw rnstatus records.
"""
import ipaddress
import re

# Reticulum Interface.Interface constants used by rns 1.5.x. Keep the numeric
# value separately so an unknown future value remains visible instead of guessed.
MODE_NAMES = {
    1: "Full",
    2: "Point-to-Point",
    3: "Access Point",
    4: "Roaming",
    5: "Boundary",
    6: "Gateway",
    7: "Internal",
}

# rns 1.5.7 names inbound TCP children this way. This deliberately accepts
# IPv4 only. If a future rns release formats IPv6 differently, leave the
# endpoint unavailable rather than guessing an address from arbitrary text.
INBOUND_TCP_CHILD = re.compile(
    r"^TCPInterface\[Client on (?P<label>[^/\[\]]+)/(?P<ip>(?:\d{1,3}\.){3}\d{1,3}):(?P<port>[1-9]\d{0,4})\]$"
)


def values(item, *names):
    if not isinstance(item, dict):
        return None
    for name in names:
        value = item.get(name)
        if value is not None:
            return value
    return None


def as_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def mode_display(value):
    numeric = as_int(value)
    if numeric is not None:
        return MODE_NAMES.get(numeric, f"Unknown ({numeric})")
    if value is None:
        return "—"
    return str(value)


def status_display(value):
    if value is True:
        return "Up"
    if value is False:
        return "Down"
    if value is None:
        return "—"
    return str(value)


def ifac_enabled(raw):
    """Only report enabled when rnstatus supplies an IFAC-related value."""
    fields = ("ifac_signature", "ifac_size", "ifac_netname")
    if not any(field in raw for field in fields):
        return None
    return any(raw.get(field) is not None for field in fields)


def parse_inbound_tcp_endpoint(name):
    """Return an exact Reticulum 1.5.x inbound TCP endpoint, or None."""
    match = INBOUND_TCP_CHILD.match(str(name or ""))
    if not match:
        return None
    try:
        address = ipaddress.ip_address(match.group("ip"))
        port = int(match.group("port"))
    except ValueError:
        return None
    if address.version != 4 or not 1 <= port <= 65535:
        return None
    return {"server_label": match.group("label"), "remote_ip": str(address), "remote_port": port}


def normalize_interface(raw):
    """Normalize one rnstatus interface record without inferring unavailable data."""
    raw = raw if isinstance(raw, dict) else {}
    raw_clients = raw.get("clients")
    client_records = raw_clients if isinstance(raw_clients, (list, dict)) else None
    name = str(values(raw, "name", "interface_name", "display_name") or "Unnamed interface")
    raw_status = values(raw, "status", "state", "online")
    raw_mode = values(raw, "mode", "interface_mode")
    return {
        "name": name,
        "short_name": values(raw, "short_name", "display_name") or name,
        "type": values(raw, "type", "interface_type", "class"),
        "enabled": values(raw, "enabled", "is_enabled"),
        "status": raw_status,
        "status_display": status_display(raw_status),
        "mode": raw_mode,
        "mode_display": mode_display(raw_mode),
        "rx_bytes": as_int(values(raw, "rxb", "rx_bytes")),
        "tx_bytes": as_int(values(raw, "txb", "tx_bytes")),
        "rx_rate": as_int(values(raw, "rxs", "rx_rate")),
        "tx_rate": as_int(values(raw, "txs", "tx_rate")),
        "client_count": as_int(raw_clients),
        "peer_count": as_int(raw.get("peers")),
        "client_records": client_records,
        "held_announces": as_int(raw.get("held_announces")),
        "tx_buffered": as_int(raw.get("txbuffered")),
        "announces": as_int(values(raw, "announces", "announce_count", "arxc", "atxc")),
        "path_requests": as_int(values(raw, "path_requests", "prxc", "ptxc")),
        "parent_interface_name": values(raw, "parent_interface_name", "parent_interface"),
        "parent_interface_hash": values(raw, "parent_interface_hash"),
        "interface_hash": values(raw, "hash", "interface_hash"),
        "ifac_netname": values(raw, "ifac_netname", "network_name"),
        "ifac_enabled": ifac_enabled(raw),
        "raw": raw,
    }


def find_interface_records(data):
    """Find and normalize interfaces across known rnstatus JSON envelopes."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in ("interfaces", "interface_stats") and isinstance(child, (list, dict)):
                    for record in child.values() if isinstance(child, dict) else child:
                        if isinstance(record, dict):
                            found.append(record)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    output, seen = [], set()
    for raw in found:
        row = normalize_interface(raw)
        key = (row["name"], str(row.get("type") or ""))
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def inbound_tcp_child_records(interfaces, configured_servers=()):
    """Identify runtime TCP children by their TCPServer parent relationship.

    A configured outbound TCPClientInterface is intentionally excluded: it has
    no parent TCPServer runtime record and does not use the inbound child name.
    """
    records = [item for item in interfaces or [] if isinstance(item, dict)]
    by_name = {item.get("name"): item for item in records if item.get("name")}
    by_hash = {item.get("interface_hash"): item for item in records if item.get("interface_hash")}
    configured_names = {
        str(item.get("name")) for item in configured_servers or []
        if isinstance(item, dict) and item.get("type") == "TCPServerInterface" and item.get("name")
    }
    children = []
    for child in records:
        if child.get("type") != "TCPClientInterface" or not child.get("parent_interface_name"):
            continue
        parent = by_hash.get(child.get("parent_interface_hash")) or by_name.get(child.get("parent_interface_name"))
        if not parent:
            # A current snapshot normally includes the parent, but accept only
            # an exact configured TCPServerInterface name embedded in the
            # documented parent form. This is not fuzzy name matching.
            parent_match = re.match(r"^TCPServerInterface\[([^/\[\]]+)/[^\]]+\]$", str(child.get("parent_interface_name") or ""))
            if parent_match and parent_match.group(1) in configured_names:
                parent = {"name": child["parent_interface_name"], "short_name": parent_match.group(1),
                          "type": "TCPServerInterface", "interface_hash": child.get("parent_interface_hash")}
        if not parent or parent.get("type") != "TCPServerInterface":
            continue
        endpoint = parse_inbound_tcp_endpoint(child.get("name"))
        if not endpoint:
            continue
        parent_labels = {str(parent.get("short_name") or ""), str(parent.get("name") or "")}
        bracketed = re.match(r"^[^\[]+\[(.+)\]$", str(parent.get("name") or ""))
        if bracketed:
            parent_labels.add(bracketed.group(1).rsplit("/", 1)[0])
        if endpoint["server_label"] not in parent_labels:
            continue
        children.append({"runtime": child, "parent": parent, "endpoint": endpoint})
    return children
