# Clinical Planner Contract

The `/api/knowledge/planner` endpoint returns the selected knowledge node plus a derived `clinical_plan` object. This keeps clinical reasoning close to the knowledge base and prevents the frontend from inventing meaning from labels.

## Source Collections

The planner derives its output from:

- `knowledge_nodes`
- `nerve_roots`
- `cranial_nerves`
- `muscles`
- `electrode_coordinates`
- `protocols`

Protocol documents are resolved from three sources, in order: direct `linked_protocols` on the selected node, inherited `linked_protocols` from parent decision-tree nodes, and `default_for_domains` protocol documents for the selected node domain.

## Main Fields

```json
{
  "clinical_plan": {
    "readiness": {
      "state": "ready | review | action_needed | navigation",
      "label": "Setup-ready",
      "detail": "Human-readable status"
    },
    "coverage": {
      "roots": [],
      "cranial_nerves": [],
      "muscle_count": 0,
      "mapped_muscle_count": 0,
      "bilateral_muscle_count": 0,
      "pin_count": 0,
      "protocol_count": 0
    },
    "channel_plan": [],
    "gaps": [],
    "traceability": {
      "sources": [],
      "derived_from": []
    }
  }
}
```

## Body-Map Pins

Each planner response also returns `pins`. Pins are normalized percentage coordinates over `body.jpg`, so the same points scale with the rendered image size.

Each pin includes:

- `lat_name` and `name_el`
- `placement` and `action`
- `roots` and `cranial_nerves`
- `side`, `view`, `x`, `y`
- `coordinate_status`, `coordinate_system`, `coordinate_source`, `image`

The frontend uses these fields for realtime hover/focus tooltips on the anatomical map. When new coordinates are added, they must be curated against the actual image anatomy and committed through `ionm-coords.json` rather than hard-coded in the UI.

The body map also exposes calibrated focus views over the same image: full body, head/neck, upper limb, lumbosacral, lower limb and posterior. These views crop `body.jpg` and project the same normalized coordinates into the active viewport; they do not create separate alternate coordinates.

The current `body.jpg` is adequate for workflow validation, but its resolution is low for production zoom. Before public professional use, replace it with a curated high-resolution anatomical asset and re-check/re-curate `ionm-coords.json` against that exact image.

Validate coordinate integrity after edits:

```bash
node tools/validate_coords.mjs
```

## Readiness Meaning

- `navigation`: the node has children and acts as a decision step.
- `ready`: linked anatomy, muscles, coordinates and protocols are internally consistent for the current knowledge base.
- `review`: the node is usable but exposes a knowledge-base item that should be clinically reviewed.
- `action_needed`: required mapping is incomplete, such as missing body-map coordinates for a linked muscle.

## Current L4-L5 Baseline

For `live.spine.lumbar.disc.l4-l5`, the current local seed returns:

- 2 linked roots: `L4`, `L5`
- 5 channel-plan muscles
- 10 body-map pins
- 1 resolved protocol: `protocol.spine.mep_emg.setup`

The planner still surfaces knowledge-base gaps when coordinates, channels or protocol coverage are genuinely missing.