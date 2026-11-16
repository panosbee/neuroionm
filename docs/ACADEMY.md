# Academy Contract

The Academy uses the same knowledge-base, planner and body-map coordinate data as the live workstation. It must not maintain a separate simulated anatomy model.

Lesson evidence uses the shared planner protocol resolver. Protocols may be linked directly to the selected node, inherited from a parent node, or supplied as domain-default protocol documents such as spine MEP/EMG setup and cranial nerve EMG setup.

## API Surface

- `GET /api/academy/curriculum`
  - Returns curriculum modules, linked lessons and a simulation library summary.
- `GET /api/academy/learners`
  - Returns Admin-created learner profiles that can be selected in Academy.
- `POST /api/academy/learners/select`
  - Stores the selected learner in an HTTP-only Academy cookie.
- `GET /api/academy/progress/lessons/{node_key}` / `PATCH /api/academy/progress/lessons/{node_key}`
  - Loads and updates Mongo-backed lesson progress.
- `GET /api/academy/progress/report`
  - Returns learner report data: passed lessons, mistakes, review targets, module completion and average scores.
- `GET /api/academy/lessons/{node_key}`
  - Returns a structured lesson package with learning path, objectives, competencies, checklist, checkpoints and case scenarios.
- `POST /api/academy/checkpoints/grade`
  - Grades learner checkpoint selections against backend-generated answer keys derived from the lesson package.
- `POST /api/academy/cases/grade`
  - Grades selected case-scenario actions against the scenario rubric and persists the attempt for the learner.
- `GET /api/academy/simulations`
  - Lists knowledge nodes that have mapped muscles/electrodes and can be used as placement labs.
- `GET /api/academy/simulations/{node_key}`
  - Returns the selected node, targets, channel plan and protocols for one simulation.
- `POST /api/academy/simulations/validate`
  - Scores learner electrode placements against the normalized body-map targets.
- `GET /api/academy/waveforms/{node_key}`
  - Returns deterministic training waveform scenarios for the selected lesson/node. Supports adjustable `amplitude_drop_percent`, `latency_shift_ms`, `threshold_ma` and `noise_scale` query parameters.
- `POST /api/academy/waveforms/quiz/grade`
  - Grades waveform recognition questions and persists score/recommendations.
- `GET /api/academy/bibliography/{node_key}`
  - Searches PubMed through NCBI E-utilities with Mongo cache and returns lesson-relevant literature links.
- `POST /api/academy/tutor/ask`
  - Academy-specific AI tutor endpoint using selected lesson context, resolved protocols, KB/RAG evidence, PubMed bibliography and current placement/waveform/case mistakes.

## Simulation Rules

Each simulation target is generated from planner pins. A placement attempt includes:

```json
{
  "target_id": "Biceps brachii:left:anterior",
  "x": 42.5,
  "y": 34.2
}
```

Validation reports:

- `correct`: within the configured normalized tolerance.
- `near`: outside tolerance but close enough to refine.
- `off`: placement should be repositioned before accepting the channel.
- `unknown_target`: submitted target is not part of the selected simulation.

The signal panel is a training signal-quality model based on placement distance. It is intended to teach correct spatial setup and noise risk; it is not a substitute for a real amplifier or patient signal.

## Waveform Lab

The Waveform Lab is served inside `/academy.html` as a separate Academy learning surface. It generates educational traces from the selected knowledge node and planner channel plan.

Supported training modalities are inferred from the selected node:

- `SSEP` for SSEP nodes.
- `Triggered EMG` for pedicle screw stimulation nodes.
- `Cranial / RLN EMG` for cranial and thyroid nodes.
- `MEP / EMG` for spine motor/root channel plans.

Each waveform payload includes baseline, amplitude-drop, latency-shift, combined-warning, threshold-change, anesthetic-suppression, artifact/noise and stimulation/acquisition-failure scenarios. Each scenario returns channel traces, amplitude, latency, noise, signal-to-noise and threshold data where applicable. These are synthetic educational traces for interpretation training, not patient/device recordings.

The Waveform Lab also includes a scored quiz for artifact recognition, warning-pattern recognition and technical-first escalation checks.

## Learner Progress And Report

Academy progress is stored in MongoDB, not browser localStorage. The primary collections are:

- `academy_progress`: one document per learner and lesson/node.
- `academy_attempts`: timestamped placement, checkpoint, waveform quiz and case scenario attempts.

The learner report records:

- lessons started and completed
- average score
- lessons that passed
- mistakes in placement, checkpoint, waveform or case scenario work
- specific items that the learner should revisit
- module completion percentages

## Frontend Workflow

The Academy frontend is a separate page served from `/academy.html`. It is intentionally not embedded inside the Live workstation.

1. Open `/academy.html` from the top navigation.
2. Select a curriculum lesson or simulation library card.
3. Review lesson-level objectives, competencies, checklist, channel plan, readiness and evidence traceability.
4. Complete checkpoint questions and review backend grading feedback.
5. Start a placement lab when the selected lesson has mapped electrode targets.
6. Use the Waveform Lab to compare baseline, signal-change and artifact/noise scenarios.
7. Work through case scenarios for baseline setup, signal-change response and knowledge-base readiness.
8. Use AI Tutor, AI Coach or AI Waveform Coach with the selected node so responses stay grounded through the retrieval stack.

Progress is persisted in Mongo for the selected Admin-created learner. The checkpoint, waveform and case answer keys are generated and graded by the backend, not by frontend heuristics.

## Data Integrity

Academy simulations are only as accurate as the curated coordinates in `ionm-coords.json` and the current `body.jpg` asset. After coordinate edits, run:

```bash
node tools/validate_coords.mjs
```

The current body image has been replaced with a high-resolution CC BY 4.0 OpenStax/Wikimedia anterior/posterior asset. Asset source, license and focus-view bounds are documented in `bodymap-asset.json`.

Existing coordinates were originally curated on the previous image and must still be clinically re-curated against the new exact image before public clinical training use. Keep `bodymap-asset.json.status` as `high_res_asset_installed_coordinates_require_recuration` until that re-curation is complete; change it to `production_coordinates_curated` only after validation.

The PubMed bibliography layer returns live/cached PubMed records only. It does not synthesize citations when NCBI is unavailable or rate-limited. The Academy tutor receives PubMed titles together with KB/RAG evidence and resolved protocol context.
