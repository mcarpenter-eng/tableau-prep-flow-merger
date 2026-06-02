# Tableau Prep Flow Merger

Local web app that merges the **transform actions** from a source Tableau Prep
flow into a destination flow, while leaving the destination's environment
(node IDs, connection IDs, project, site, output names) completely untouched.

## What it's for

You have the same Prep flow (`.tfl` file) in two Tableau Cloud environments,
and someone edits the source — adds a column rename, a join, a filter, etc.
You want those *transform changes* to land in the destination, but you don't
want to re-point any connections or rename any outputs by hand.

Drop the two `.tfl` files in, click **Merge Files**, and download the result —
ready to open directly in Tableau Prep.

## Requirements

- Python 3.13 (works on 3.11+; tested on 3.13)
- macOS, Linux, or Windows

## Setup (one time)

```bash
git clone https://github.com/mcarpenter-eng/tableau-prep-flow-merger.git
cd tableau-prep-flow-merger
python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate          # if not already active
python3 app.py
```

Then open http://localhost:5050 in your browser.

## What gets merged

For each input/transform node in the source flow, the script copies its
`actions` array onto the matching node in the destination. Nodes are matched by:

- **Input nodes**: same `dbname` + `datasourceName` in `connectionAttributes`
- **Other nodes**: same `baseType`, `nodeType`, and `name`

Everything else in the destination is preserved exactly. The merged file is
identical to the destination byte-for-byte except for the `actions` arrays.

## CLI version

The same merge engine works as a CLI and accepts either `.tfl` or raw JSON:

```bash
python3 merge_flow.py \
  --source path/to/source.tfl \
  --destination path/to/destination.tfl \
  --output path/to/merged.tfl
```

Use `--dry-run` to see the merge log without writing a file.

## Files

- `merge_flow.py` — pure merge engine + CLI entrypoint
- `app.py` — Flask web UI wrapping the engine
- `templates/index.html` — drag-and-drop UI
- `examples/` — sample source/destination/merged files for sanity checking

## License

MIT — see [LICENSE](LICENSE).
