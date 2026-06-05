"""
Merge transform actions from a source Tableau Prep flow into a destination flow.

Reads the source and destination .tfl/.json files, copies the `actions` array
from each source node onto the matching destination node, and writes the result
to a new file. Everything else in the destination (node IDs, connection IDs,
project, site, output names) is preserved exactly as-is.

Both inputs can be raw JSON OR .tfl files (which are zip archives containing
a single entry named `flow`). When the destination is a .tfl, the merged output
is also written as a .tfl, preserving any other entries inside the archive.

Usage:
    python merge_flow.py --source SRC.tfl --destination DEST.tfl --output OUT.tfl
    python merge_flow.py --source SRC.json --destination DEST.json --dry-run
"""
import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

FLOW_ENTRY_NAME = "flow"


def read_flow_bytes(data: bytes) -> tuple[dict, bytes | None, str]:
    """Parse flow JSON from raw bytes (either a .tfl zip or a JSON document).

    Returns (flow_dict, archive_template, entry_name).
    archive_template is the original zip bytes (used later to preserve sibling
    entries when re-packing); it's None when the input was plain JSON.
    """
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entry_name = _find_flow_entry(zf)
            with zf.open(entry_name) as f:
                flow = json.load(f)
        return flow, data, entry_name

    flow = json.loads(data.decode("utf-8"))
    return flow, None, ""


def _find_flow_entry(zf: zipfile.ZipFile) -> str:
    """Locate the flow JSON inside a .tfl archive. Standard name is `flow`,
    but fall back to any entry that parses as a flow-shaped JSON document."""
    names = zf.namelist()
    if FLOW_ENTRY_NAME in names:
        return FLOW_ENTRY_NAME
    for name in names:
        if name.startswith("__MACOSX/") or name.endswith("/"):
            continue
        try:
            with zf.open(name) as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and "nodes" in data and "documentId" in data:
            return name
    raise ValueError(
        "Could not find a flow JSON entry inside the .tfl archive. "
        f"Entries: {names}"
    )


def write_flow_bytes(flow: dict, archive_template: bytes | None, entry_name: str) -> bytes:
    """Serialize a flow dict back to bytes. If archive_template is provided,
    returns a .tfl (zip) with the flow entry replaced and all sibling entries
    preserved. Otherwise returns plain JSON bytes."""
    flow_json = json.dumps(flow, indent=2).encode("utf-8")

    if archive_template is None:
        return flow_json

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive_template)) as src_zip, \
         zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst_zip:
        for name in src_zip.namelist():
            if name == entry_name:
                dst_zip.writestr(entry_name, flow_json)
            else:
                dst_zip.writestr(name, src_zip.read(name))
    return out.getvalue()


def load_flow(path: Path) -> tuple[dict, bytes | None, str]:
    return read_flow_bytes(path.read_bytes())


def node_fallback_key(node: dict) -> tuple:
    """Fallback match key used when source and destination node IDs don't align.

    Input nodes match on (dbname, datasourceName); other nodes match on
    (baseType, nodeType, name). Note: this key is not always unique — a flow
    can join the same Salesforce object multiple times — so callers should
    prefer node-ID matching whenever possible and only fall back to this key
    when an ID match isn't available.
    """
    base_type = node.get("baseType")
    if base_type == "input":
        attrs = node.get("connectionAttributes", {}) or {}
        return ("input", attrs.get("dbname"), attrs.get("datasourceName"))
    return (base_type, node.get("nodeType"), node.get("name"))


def merge_actions(source: dict, destination: dict) -> tuple[dict, list[str]]:
    """Return (merged_flow, log_lines). Does not mutate inputs.

    Matching strategy: when a source node's ID exists in the destination,
    apply actions directly to that node. This is the common case — flows
    typically start as copies of one another, so node UUIDs line up. Only
    when an ID match is missing do we fall back to the looser (dbname,
    datasourceName) / (baseType, nodeType, name) key.
    """
    merged = json.loads(json.dumps(destination))  # deep copy
    log: list[str] = []

    dest_node_ids = set(merged.get("nodes", {}).keys())

    # Build the fallback index from destination nodes whose IDs are NOT also
    # in the source, so a fallback match never overrides a direct ID match.
    src_node_ids = set(source.get("nodes", {}).keys())
    fallback_index: dict[tuple, list[str]] = {}
    for node_id, node in merged.get("nodes", {}).items():
        if node_id in src_node_ids:
            continue
        fallback_index.setdefault(node_fallback_key(node), []).append(node_id)

    for src_node_id, src_node in source.get("nodes", {}).items():
        src_actions = src_node.get("actions")
        if src_actions is None:
            continue  # output nodes etc. don't have an actions array

        if src_node_id in dest_node_ids:
            target_ids = [src_node_id]
            match_via = "id"
        else:
            key = node_fallback_key(src_node)
            target_ids = fallback_index.get(key, [])
            match_via = f"fallback {key}"

        if not target_ids:
            if src_actions:
                log.append(
                    f"WARN: source node '{src_node.get('name')}' "
                    f"({src_node_id}) has {len(src_actions)} action(s) but "
                    f"no matching destination node ({match_via})."
                )
            continue

        if len(target_ids) > 1:
            log.append(
                f"WARN: source node '{src_node.get('name')}' matched "
                f"{len(target_ids)} destination nodes via {match_via}; "
                f"applying actions to all."
            )

        for dest_node_id in target_ids:
            dest_node = merged["nodes"][dest_node_id]
            before = dest_node.get("actions", []) or []
            dest_node["actions"] = json.loads(json.dumps(src_actions))
            log.append(
                f"OK ({match_via}): '{src_node.get('name')}' -> "
                f"'{dest_node.get('name')}' "
                f"({len(before)} action(s) -> {len(src_actions)} action(s))"
            )

    return merged, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Where to write the merged flow. Defaults to <destination>.merged.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merge log without writing an output file.",
    )
    args = parser.parse_args()

    source, _, _ = load_flow(args.source)
    destination, dst_archive, dst_entry = load_flow(args.destination)
    merged, log = merge_actions(source, destination)

    for line in log:
        print(line)

    if args.dry_run:
        return 0

    is_tfl = dst_archive is not None
    if args.output:
        out_path = args.output
    else:
        suffix = ".merged.tfl" if is_tfl else ".merged.json"
        out_path = args.destination.with_suffix(suffix)

    out_path.write_bytes(write_flow_bytes(merged, dst_archive, dst_entry))
    print(f"\nWrote merged flow to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
