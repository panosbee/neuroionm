# Product Principles

The platform is built as a professional IONM working tool, not as a demo interface.

## Direction

- Prioritize fast clinical orientation: the user should immediately see the selected anatomy, coverage, channel plan, coordinates, protocols and open knowledge gaps.
- Keep the decision tree as the main navigation model, because it mirrors procedural decision-making better than disconnected tabs.
- Prefer derived clinical signals from the knowledge base over UI-only labels. If the UI shows readiness, coverage or gaps, the backend should compute them from stored data.
- Treat missing protocol links, missing electrode coordinates and sparse terminal nodes as visible knowledge-base work items.
- Keep case workflow non-identifying by default. Case references and event notes must not require personal data.
- AI assistance must stay grounded in selected node, case timeline, linked channels and protocols. It should explicitly say when the knowledge context is insufficient.
- AI retrieval must use semantic embedding search over curated knowledge-base records. Do not replace it with keyword-only matching for clinical/scientific reasoning.

## UI Standard

- The first screen should feel like a workstation: dense, calm, scannable and action-oriented.
- Use cards only for bounded functional units such as clinical sections, case controls, body map and assistant panels.
- Avoid decorative visuals that do not help planning, monitoring, teaching or auditability.
- Every interactive control should have a clear operational purpose: choose node, create case, record event, ask a grounded question or inspect anatomy.

## Clinical Data Standard

- Do not add mock clinical data, placeholder thresholds or fabricated protocols.
- Seeded data must come from the current reference files or from explicit curated additions.
- Any new clinical rule should include source traceability and should be represented in Mongo, not hard-coded only in the frontend.
- If scientific confidence is incomplete, show that as a review item instead of hiding it.