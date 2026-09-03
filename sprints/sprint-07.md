# Sprint 7 — Granola & Call Intelligence

**Objective**: Every call becomes property-segmented, enriched, auto-analyzed evidence — within minutes, untouched by human hands.

## Work Items

### Connectors
- [ ] Granola connector: webhook primary, polling fallback, **idempotent by meeting ID**
- [ ] Zoom through the same pipeline

### Property Segmentation (the specific-properties-only guarantee)
- [ ] Multi-job calls split into **property-tagged segments**; low confidence → review queue
- [ ] Cross-property leakage structurally impossible (chunk-level property tags, same rule as email)
- [ ] Labeled call set built for the segmentation gate

### Enrichment (per segment)
- [ ] Decisions · commitments (into the commitment sweep) · verbal dollar claims (detector fuel for Sprint 10/11) · red flags · disputes · tone
- [ ] Speaker-turn-aware chunking; 3-tier context; full chunk schema

### Auto-Analysis Integration
- [ ] Finished call triggers the full pipeline for its properties: **within minutes** — timeline events, commitments tracked, cards pushed, no one asked

### Meeting Support
- [ ] Pre-meeting prep packs **30 minutes before** contractor calls
- [ ] Post-call automatic loop closure (commitments vs prior open loops)

## Gate
- [ ] Segmentation **≥ 90%** on the labeled set
- [ ] Call commitments aging correctly in waiting-on views
- [ ] Citations open to **exact transcript lines**
- [ ] A **live test call** flows through the entire auto-analysis chain untouched by human hands

## Added Features
