# Notes analysis (H&K Homes portfolio, 16 Oct 2026)

One-off market analysis of the ZIP codes behind `Note_Portfolio_Report_HK HOMES.xlsx`.
Published page: https://claude.ai/artifact/6L3MeWsz7uD5F418dEsHTC

Pipeline (run from a scratch folder holding the downloaded CSVs; see URLs in extract.py and build_report.py):
1. `extract.py`  - pulls the portfolio ZIPs, metros and states out of the Zillow ZHVI / ZHVF / ZORI / inventory CSVs -> extract.json
2. `analyze.py`  - yearly values, peak/drawdown, trend gap, rents, note-level LTV and payments behind -> analysis.json
3. `build_report.py` - five-year projection model (base / bear / bull) per ZIP, metro and Texas -> report_data.json
4. `render.py` + `template.html` -> hk_notes_outlook.html

FRED series (MORTGAGE30US, TXSTHPI, ATNHPIUS41700Q, FEDFUNDS, CPIAUCSL) are blocked from the office network; fetch them elsewhere.
FHFA ZIP5 index: https://www.fhfa.gov/hpi/download/annual/hpi_at_zip5.xlsx (40 MB, header on row 6).
