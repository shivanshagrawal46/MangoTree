# ARV & Underwriting — After Repair Value, appraiser-grade, for the United States

*Added 2026-10-14. Lives in `mangotree/arv/`, `mangotree/api/arv_routes.py`, `web/src/components/arv/`, `web/src/app/(app)/arv/`. Nothing else in the system was changed beyond two lines registering the routes and one line in the sidebar.*

## What it does

Give it a US address and what you know about the house. It returns:

1. **ARV by sales comparison** — the Form 1004 / URAR method in code. Comparable sales are found, scored for similarity, adjusted line by line to the subject *as repaired* (time/market conditions from the ZIP home-value index, living area, bedrooms, baths, garage, basement, pool, lot, age, UAD condition C1–C6), reconciled by weight into a value with a range, cross-checked by $/sqft, blended with an AVM when one is available, and graded for confidence (A–E, 0–100, an FSD-style ± band) with the reasons listed.
2. **The MAO Rehab Calculator** — the team's spreadsheet (`MAO_Rehab_Budget_Calculator.csv`) as a live model: buying, holding, selling and financing costs; rehab by scope tier × state cost index or by line item, with permit / OH&P / contingency adders; MAO by the 70% rule *and* by profit target (solved so the chosen target profit remains after every cost); profit, profit per month, cash ROI, annualized ROI, capital stack; LTARV / LTC / margin / rehab-ratio checks; a 5×5 sensitivity grid of profit against ARV and rehab misses. Exportable as `.xlsx` with live formulas, laid out like the CSV.
3. **A Claude Opus 5.5 review (effort high)** — reads the entire file and returns a structured opinion: its own ARV and range, whether it agrees with the engine, comp-by-comp keep/drop/caution notes (with one-click "exclude" / "set condition" in the UI), a critique of the adjustment rates, a market read, a rehab read, a maximum-offer opinion, risks, and **the questions whose answers would move the number most**. Answer them on screen and re-run; or ask anything about the file in a streamed chat.
4. **A PDF report** anyone can forward: headline values, comps grid, the full adjustment grid, market, rehab, deal, and the analyst's review, with the data sources named.
5. **Documents read into the file** (added 2026-10-14, second pass). Inspection reports, appraisals, contractor bids, listing sheets, photos, tax bills, title work and purchase contracts are stored once by content hash, read natively (PDF text layer, Word, Excel, CSV) or as images (scans, photos, HEIC) and then read by Claude Opus 5.5 into a fixed shape: document type, the facts it states about the house, every dollar figure, priced repair items mapped to the calculator's trades, defects with an underwriter's severity, the comparable sales in an appraisal grid, what the photos show, and what the document leaves open. Nothing is applied silently: the intake offers each fact with its source and a "use" button, a bid can replace the tier estimate as the budget, an appraisal's comps can be added to the grid, and the analyst sees every document's digest and cites it by name. About $0.05–0.10 per document.

## The intake

Six steps, in a fixed order, so no file arrives half-described: **Address** (Census geocoder, property type, how the deal comes in) → **Documents** (drop zone; facts found are offered, conflicts between documents are flagged) → **The house** (rooms, structure, systems, site, UAD condition today, occupancy, known issues as tick-boxes, notes) → **The plan** (planned work as tick-boxes; budget by tier, by a contractor's bid from the documents, or your own number; the house *as it will be* — bedrooms, baths, area, condition) → **The deal** (asking and purchase price, exit, months, 70% rule and profit target, commission, insurance, taxes, hard-money terms) → **Review & run**. The right rail shows the file as it builds, with a completeness meter and what is still missing. When the plan adds a bedroom, a bath or living area, the engine values the house with the after values (`as_repaired()`), and the analyst is told so.

Every input is editable afterwards — toggle a comp, type a condition, change a rate, add a line item — and the deterministic half recomputes on the server in one round trip. The model is consulted for judgement, never for arithmetic.

## Where the data comes from (free first, keys optional)

| Need | Source | Key | Notes |
|---|---|---|---|
| Address → coordinates, county, census tract | **US Census Geocoder** | none | Primary. OpenStreetMap Nominatim as fallback. US only, enforced. |
| Comparable sales | **Claude web search** (Opus 5.5 with `web_search_20260209`) | none | Finds recently sold homes on public listing pages, reports them in a fixed shape with the page URL and `price_basis` (closed / listed / estimated). Weighted down and labelled *web* until verified. |
| Comparable sales, public record, AVM | **RentCast** `/properties`, `/avm/value` | `RENTCAST_API_KEY` | Free developer tier: 50 calls/month. Fills blanks in the subject (beds, baths, sqft, lot, year, tax bill), provides MLS-derived comparables with a correlation score and an AVM to blend. Comparable `price` is the listing price unless a closed sale is known — labelled. |
| Comparable sales | **Manual** | none | A sale from the MLS, a CMA or the county record, typed in. Geocoded and scored like the rest. |
| Time adjustment index | **Zillow ZHVI** by ZIP (public CSV, ~120 MB, cached daily under `raw_store/arv_cache/`) | none | Mandatory since March 2025 for appraisals; each sale is walked forward at the ZIP's monthly rate. |
| Mortgage rate | **FRED** MORTGAGE30US | none | |
| Flood zone | **FEMA NFHL** | none | SFHA flag → lender needs flood insurance. |
| Neighbourhood | **Census ACS** 5-year, tract | none | Median value, income, owner share, vacancy, year built. |

Any source that does not answer produces a labelled gap, never a zero.

## Method notes (what the research decided)

- **Comp selection**: default 1 mile / 12 months / same type / GLA within 30%, widened automatically (and said so) when fewer than three qualify. Similarity 0–100 with itemised deductions. Fannie Mae has no one-mile rule and no GLA rule; these are lender conventions and the UI lets you change them.
- **Adjustments**: GLA rate from a paired-sales regression across the selected comps, bounded to 25–45% of median $/sqft (the land and the core are not bought by the foot). Baths $3–12k, half baths $1.5–6k, garage $3–10k/bay, bedrooms small (GLA carries most), basement and pool bounded, lot beyond ±15% at a $/sqft derived from price level and capped at 10%, age 0.25%/yr capped at 10%, condition 4% per C-step. Every line shows its rate and reason so the analyst can argue with a specific number.
- **Net 15% / gross 25%**: Fannie Mae dropped this as a rule in December 2014. It is reported as a soft heuristic and lowers a comp's weight; it never excludes one.
- **Reconciliation**: weight = similarity² × (1 − gross%) × recency, web comps ×0.75, forced-in comps ×0.6. Value = 70% weighted mean + 30% weighted median; range = one weighted standard deviation (min 3%). Bracketing on size and value is reported.
- **AVM blend**: 20% weight if within 15% of the sales comparison, otherwise not blended and flagged.
- **Confidence**: comp count, mean similarity, dispersion (CV), average gross adjustment, data quality by source, bracketing, widening penalty → 0–100 → grade → ± band (A 4%, B 7%, C 11%, D 16%, E 22%).
- **Rehab tiers (2026, investor grade)**: cosmetic $15–40/sqft, moderate $35–80, full gut $70–145, gut + addition $150–300, × a state cost index (DC 1.14, MD 1.03, VA 0.96, FL 0.95 …). Line items mirror the CSV and a tier spreads into them by typical share.
- **Hard money defaults**: 85% LTC, 70% LTARV, 10.5% interest-only, 2 points, 100% of rehab financed. Loan = min(LTC cap, LTARV cap); the binding constraint is named.

## Model

The analyst seat for this feature is **`claude-opus-5-5` at `output_config.effort = "high"`** with adaptive thinking (the model's default effort is medium, so it is set explicitly). Structured output is a tool schema, never hand-written JSON; the retry path nudges rather than disabling thinking (which this model rejects). Comp discovery uses the server-side web search tool with the client tool `report_comps` in the same request and loops on `pause_turn`. Spend per analysis is recorded on the document (`cost_usd`) and shown in the header. Override with `ARV_MODEL` / `ARV_EFFORT`.

## API

```
GET    /arv/status                          providers, defaults, scope tiers, line items, state cost index, portfolio addresses
GET    /arv/geocode?q=                      validate an address (422 if not in the US)
GET    /arv/analyses                        recent analyses (summaries)
POST   /arv/analyses                        {subject, assumptions, rehab, settings, property_id?, label?} → {analysis_id, job_id}
GET    /arv/analyses/{id}                   the whole file
PATCH  /arv/analyses/{id}                   subject / assumptions / rehab / settings / comps[{comp_id, included, condition, …}] / answers → recomputed file
POST   /arv/analyses/{id}/comps             add a manual comp → recomputed file
DELETE /arv/analyses/{id}/comps/{comp_id}
POST   /arv/analyses/{id}/rerun             {stages: [geocode|records|market|comps|analyst]} → job
POST   /arv/analyses/{id}/chat              {question} → job streaming `delta` events
GET    /arv/analyses/{id}/report.pdf
GET    /arv/analyses/{id}/calculator.xlsx
DELETE /arv/analyses/{id}

POST   /arv/documents                       multipart files[] (+ analysis_id?, hint?, address?) → {job_id, documents[]}; staged when no analysis_id, attached otherwise
GET    /arv/documents/{doc_id}              the digest (structured read + text excerpt)
GET    /arv/documents/{doc_id}/file         the original bytes
POST   /arv/analyses/{id}/documents/{doc_id}/apply   {what: facts|rehab|comps, keys?} → recomputed file
DELETE /arv/analyses/{id}/documents/{doc_id}
```

`POST /arv/analyses` also takes `document_ids` (staged uploads from the wizard). Jobs stream `phase`, `partial` (including `{key: "document"}` as each read lands), `status`, `delta`, `result`, `error`, `end` over the existing `/jobs/{id}/stream` SSE endpoint. Analyses live in `arv_analyses`; document records (with the full extracted text) in `arv_documents`; the files themselves in the object store under their sha256.

## The UI toolkit

The ARV screens are built on **Ant Design 6** (tables, tabs, steps, modals, dropdowns, upload, statistics, segmented controls, collapse), themed in `web/src/app/(app)/arv/layout.tsx` with a `ConfigProvider` whose tokens repeat MangoTree's palette, radius and type, and a dark algorithm that follows the app's `.dark` class. Nothing outside `/arv` uses antd; the rest of the app stays on Tailwind + Radix. Two facts worth knowing: antd's styles are not in Tailwind's cascade layer, so a Tailwind utility on an antd root loses unless it is `!important` (the `Card` shim in `components/arv/ui.tsx` puts layout classes on an inner div for that reason); and the few antd-specific placements live in `arv.css`, imported by that layout.

## Speed

Run the app with `scripts\serve.ps1` (production build of the web app on :3000 plus the API), not `scripts\dev.ps1`, when you are using it rather than changing it: `next dev` compiles each page the first time it is opened, which reads as slow clicks and blank pages. Measured on the built app: list 0.4-0.6 s, opening a file about 1.2 s cold (the first six files' data is prefetched while the list is on screen, and a row's data on hover), tab switches 0.05-0.3 s once the tab code has been warmed in the background. Tab switches run inside a React transition so the loading fallback (and React's 300 ms fallback throttle) never shows. On the API, `ArvStore` keeps the last 64 files in an in-process cache (every write goes through it, callers get copies), so a file read is 0.26 s instead of 1.1 s of Atlas round trips.

## Reading the review

The analyst returns, besides its prose, `summary_points` (3–7 one-sentence bullets, each with a number or a named comp or document), `value_drivers` (signed dollar effects on the ARV, largest first), and a lending `verdict` (fund / fund with conditions / renegotiate / pass) with a one-line reason. The overview shows these as a verdict strip, numbered points, and a bar chart; the prose is behind "show the full reasoning". The web-search notes are split into sentence bullets with the first three visible.

## Tests

`tests/test_arv_engine.py` pins the arithmetic: selection and widening, adjustment direction, include/exclude/force, AVM blend and divergence, the calculator against the CSV's own example (ARV $800,000, repairs $204,000 → MAO $356,000, buying $2,500, holding $11,520), MAO-by-profit-target leaving the target, rehab modes, and comp de-duplication.

## Disclaimer carried on every report

An automated sales-comparison opinion for underwriting support, not an appraisal under USPAP. Comps marked *web* are unverified against MLS or county records.
