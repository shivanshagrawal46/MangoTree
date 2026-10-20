# Sprint 12 — Forecasting, Contractor Forensics, Hardening & Launch

**Objective**: The system prices every deal, profiles every contractor, survives every attack we can think of — and proves it two weeks straight.

## Work Items

### Forecasting
- [ ] Actuarial baselines from closed deals
- [ ] **Nightly Monte Carlo per property**: profit bands, loss probability, top drivers, **visible assumptions**
- [ ] Deal analyzer with policy pre-check and contractor-adjusted bids
- [ ] Profit bands feed back into Sprint 8's overview and Sprint 9's portfolio grid

### Contractor Forensics
- [ ] Forensic profiles: bid-vs-actual · timeline multipliers · change-order habits · billing-ahead incidents · say-vs-invoice consistency
- [ ] **Promises-vs-performance**: investor packages vs realized outcomes
- [ ] **Counterfactual replay memo in dollars**

### Red-Team Week
- [ ] Contradictions · absent data · cross-property leaks · **prompt-injection strings inside emails, PDFs, and photo captions** — document content is evidence, never instructions

### Hardening
- [ ] Load testing · kill-and-resume across all lanes
- [ ] **Backup-restore verified by re-running the golden set against the restored copy**
- [ ] Role audit across all three users · outbound whitelist audit · secrets rotation · runbook

### The Weekly Ritual (institutionalized, forever)
- [ ] **20 rotating golden questions human-graded Fridays; scorecard Mondays**

## Gate — Launch Scorecard (green two consecutive weeks)
- [ ] recall ≥ 98%
- [ ] Exhaustive completeness 100%
- [ ] Verification ≥ 97%
- [ ] Zero silent degradations
- [ ] Latency: email < 60 s · calls < 10 min · photos < 5 min · answers < 45 s · dashboards < 3 s
- [ ] Corrections + Remember notes applied 100% on scope match
- [ ] Briefs 10/10 on time
- [ ] Task precision ≥ 85%
- [ ] Draw audits auto-generated on 100% of requests
- [ ] Policy deviations flagged with citations
- [ ] Uptime ≥ 99.5%
- [ ] Human panel ≥ 9.5/10

## Added Features

- [x] **ARV & underwriting platform** (added 2026-10-14, admin ask: "world's best ARV analysis platform", Claude Opus 5.5 high as the analyst). Address + details in → appraiser-style sales comparison (comp scoring, line-by-line adjustments to the subject as repaired, weighted reconciliation, confidence grade), the MAO Rehab Calculator in code (fixed costs, rehab tiers × state index, 70% rule and profit-target MAO, ROI, capital stack, sensitivity grid), free US data (Census geocoder, Zillow ZHVI, FRED, FEMA, ACS; RentCast optional), Claude web search for recent sales, Opus 5.5 structured review with questions-for-the-user and a streamed chat, PDF and .xlsx exports. See `docs/14-ARV-SYSTEM.md`. Tests: `tests/test_arv_engine.py`.
- [x] **ARV intake, documents and a scannable review** (added 2026-10-14, same day, admin feedback: the analysis read "like an essay", take more details in a structured way, ingest property documents). Six-step intake wizard (`/arv/new`) with a completeness meter; documents (inspection, appraisal, bid, listing, photos, tax, title, contract) stored once and read by Opus 5.5 into facts / dollar figures / priced items / findings / comps, offered with their source and applied on click (`mangotree/arv/documents.py`, `POST /arv/documents`, `.../documents/{id}/apply`); subject gains structure, systems, site, occupancy, known issues, planned work and as-repaired rooms/area (the engine values the after state); the review gains `summary_points`, `value_drivers` and a lending `verdict`; the overview and list page were rebuilt around numbers, bullets and pass/fail checks instead of paragraphs.
- [x] **ARV polish after the first walk-through** (added 2026-10-14, same evening, admin feedback: no delete on the list, pages "don't load" without a reload, overview and review cluttered, field labels too light). Fixed: the running job's id was wiped by the first save so an open page could not follow a run (routes keep it on the in-memory doc; orphaned runs are marked failed at startup); web search 400 when the geocoder returned no city (empty `user_location` fields omitted); `loading.tsx` / `error.tsx` under `/arv` so every click answers at once; heavy tabs load on demand; delete from the list rows; labels darkened; overview and review folded into one-line disclosures with previews; RentCast key activated and verified (2 calls per analysis).
- [x] **ARV screens on Ant Design 6** (added 2026-10-14, admin ask: "use ant d, it will give a proper admin like designing, keeping the colour scheme same"). `antd@6`, `@ant-design/icons@6`, `@ant-design/nextjs-registry` added; a `ConfigProvider` in `app/(app)/arv/layout.tsx` maps the MangoTree tokens (light and dark); `components/arv/ui.tsx` re-exports the shared primitives on antd so every ARV screen switched at once; the list and comps grids became sortable antd Tables with expandable rows, the wizard stepper antd Steps, the run progress antd Steps, actions a Dropdown, deletes Popconfirm, documents an Upload dragger, folded sections Collapse, numbers Statistic. The rest of the app is untouched.
- [x] **ARV speed** (added 2026-10-14, admin: "clicking on things takes time, optimise the full ARV code"). Measured first: API payloads are small (50 KB) and the delay was `next dev` compiling routes on demand plus a 1.1 s Atlas read per file. Fixes: `scripts/serve.ps1` runs the production build; tab switches inside `startTransition` with tab modules preloaded after mount (no Suspense fallback, no 300 ms React throttle); list prefetches the first six files' data and route, and a row's data on hover; file and list queries cached 5 min / 1 min with no focus refetch; `ArvStore` in-process LRU cache (64 files, copies out, every write updates it). Result: file read 0.26 s, tab switch 0.05-0.3 s, list 0.4 s.
- [x] **Unusual houses no longer empty the grid** (2026-10-14 evening, from the Unionville farm file: 8,947 sqft composite, 8 bd). RentCast's AVM returns 400 "insufficient comparables" for a size/room filter nothing nearby matches: the client now retries with no size or room filter and a wider radius, keeps the comparables for the grid and drops the value with a note. The engine's 40% living-area hard filter relaxes to 60% and then drops (fit points still deducted) before returning "no comparable sale qualified", with a note telling the person to check the living area. Test added (10 pass).
- 2026-10-15: ARV screens say "AI" instead of the model name and effort (list header pills, wizard run card, analyst card, document dropzone, run-progress phase labels, spend lines). The model is still chosen by ARV_MODEL / ARV_EFFORT and recorded on each analysis.
- 2026-10-15: **A bid on file is the budget by default.** The admin could not find "Use the bid on file" on the second Unionville file: the button only showed when the budget was $0, and the tier estimate had filled it with $529,090, so the MAO went negative while a $190,963 bid sat on the Documents tab. Now a document with priced items becomes the rehab budget the moment it is read (wizard: budget source flips to the bid; workspace: attached uploads adopt it; a contractor_bid replaces a budget adopted from an investor pack), unless a person already chose a lump sum, edited lines or another bid. The calculator's basis line names the bid; the Documents tab marks the bid the budget follows; the workspace warns when a bid is on file but the budget is still a tier estimate.
- 2026-10-15: **Run waits for the documents.** Clicking Run while a staged upload was still being read created the file with a dead "reading" copy of the document (the staged read job never updates the file), so the AI review, the bid adoption and fact apply never saw it. Admin chose the simple fix: the wizard's Run button is disabled and reads "Reading 1 of 2…" until every document is read; the line under it says why.

## 2026-10-16 - Notes analysis section

- New sidebar entry "Notes analysis" (/notes). Interactive version of the H&K Homes note-portfolio research: summary, five-year history (metro index chart with legend toggles and zoom, ZIP table, FHFA index), ZIP explorer (one ZIP with fair value and the three five-year paths, FHFA vs Zillow bars, compare mode), next five years (ranked bars by scenario, San Antonio and Texas fans, model inputs, outside forecasters), Texas (state comparison, metro supply, drivers), rates and loans (mortgage and Fed chart with events, forecasts, investor pricing, payment calculator, refinance incentive per note), note by note (sortable table, value-stress slider, watch list, balance-vs-LTV scatter), sources.
- Backend: mangotree/notes (routes.py: GET /notes/analyses, GET /notes/analyses/{id}; datasets/*.json). Dataset built by scripts/notes_analysis/make_dataset.py from the Zillow, FHFA and FRED pulls; the scripts and README are in scripts/notes_analysis.
- Frontend: web/src/app/(app)/notes (layout re-exports the ARV antd theme), web/src/components/notes/*, web/src/lib/notes.ts. Recharts on the app's CSS variables, so dark mode follows.
- Existing code touched: two lines in mangotree/api/app.py, one nav line and one icon import in web/src/components/shell.tsx.

## 2026-10-20 - ARV comps on closed prices and a condition grade

- Why: on the five files, 71 of 123 comps carried a listing price (RentCast) and 10 had a condition grade. Admin: "we have to know the condition of the sold property as well else our engine will give the wrong ARV".
- Two paid feeds, both keyed in .env (REALESTATEAPI_KEY, HOUSECANARY_API_KEY + HOUSECANARY_API_SECRET), new module mangotree/arv/feeds.py:
  - RealEstateAPI: the subject's public-record file (deed history with arm's-length flags, every mortgage with the lender, MLS history, owner, tax, vacancy and distress flags) and the recorded sales within the radius (deed price and date, MLS sold price where one exists). Durham St: 26 recorded sales in a mile, and RKB's own $156,649 construction loan on the file.
  - HouseCanary (Basic plan, Analytics API billed per property): details_advanced (condition grade C1-C6, verified living area, rooms) and sales_history for the chosen comps and the subject only. About $6 a deal. Admin chose the standard set only; no AVM or six-conditions call.
- Comp selection is now three steps (pipeline._shortlist_and_verify): rules score and filter as before (plus a non-arm's-length deed is a hard fail); Opus 5.5 reads deed and MLS evidence for the top 10 by fit plus the best-priced closed sales and keeps five with a reason each, setting the rest aside with a reason (analyst.shortlist); HouseCanary grades the five and the subject. After a shortlist the grid is the shortlist: nothing is padded in, a thin grid shows as low confidence. Admin can switch any set-aside comp back on; "Re-pick the comps" re-runs the last two steps without a new search (rerun stage "shortlist").
- Screens: Comps tab opens with "How the comps were chosen" (the three steps, the analyst's note, picks with reasons, set-aside list, data cost); rows carry analyst pick / set aside / deed verified / flip resale / not arm's length pills and the source of the condition grade (HC, AI); expanded rows show the deed and MLS evidence and HouseCanary's grade against the county's word. Overview gains "Property file" (HouseCanary grade vs county condition, owner, last market sale, MLS, tax, open mortgages table, deed and MLS history, flags). Wizard has switches for both feeds. New file web/src/components/arv/feeds-ui.tsx; small edits in arv.ts, comps.tsx, overview.tsx, workspace.tsx, wizard.tsx.
- Durham St after the change: 5 analyst picks (Llewelyn flip resale $230K, Lafayette $255K, Eden $269.9K, E Federal $235K, Lanvale $39.9K as the as-is floor), all five graded by HouseCanary, subject graded C3 (county: Poor), ARV $221,700 (202,300-241,100) moderate confidence, against $110,000 before from a listing-price grid. Foreclosure trustee deeds, auctions, a $7,000 city disposition and stale $150K listings were set aside with reasons. Admin will run the other files.
