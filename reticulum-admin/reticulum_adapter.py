"""Stable, defensive models for Reticulum utility JSON.

The rnstatus CLI is the source of truth, but its JSON field names and optional
values are CLI-version/interface-specific. Templates consume only these models,
never raw rnstatus records.
"""

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
