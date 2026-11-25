# MAKOS demo data

Everything in `data/processed/` is built from **real public Indian data** by the scripts in `scripts/`.
`data/raw/` holds the large downloads and is not committed (about 7 GB).

| File | Built from | Script |
|---|---|---|
| `processed/makos.db` | MCA company master, CPPP award-of-contract records, NSE/SEBI and World Bank debarment lists | `scripts/build_dataset.py` |
| `processed/gfr_clauses.json` | General Financial Rules 2017 (updated to 31.01.2026), Dept. of Expenditure | `scripts/build_gfr_clauses.py` |
| `processed/gfr_amendment_2024.json` | DoE OM No. F.1/3/2024-PPD dated 10.07.2024 (old vs new limits), transcribed by hand from the scanned OM | manual |

## Sources and licences

| Data | Source | Notes |
|---|---|---|
| Company master (CIN, status, registration date, capital) | Ministry of Corporate Affairs, published on data.gov.in (Open Government Data licence); copy taken from the Hugging Face dataset `fireboy21/tenders_aoc` (`registered_companies.csv`) | Emails and addresses dropped |
| Award-of-contract records | Central Public Procurement Portal (eprocure.gov.in), scraped and published as `fireboy21/tenders_aoc` (CC-BY-4.0) | Treated as "as published"; filtered to Telangana / Andhra Pradesh organisations, 2023 onwards |
| SEBI / NSE debarred entities | NSE list, via OpenSanctions dataset `in_nse_debarred` | Firms only (individuals removed) |
| World Bank debarred firms | World Bank, via OpenSanctions dataset `worldbank_debarred` | Indian firms only, individuals removed |
| GFR 2017 and the 10.07.2024 OM | doe.gov.in | Official PDFs |

## How to rebuild

1. Download the raw files into `data/raw/`:
   - `registered_companies.csv` and `aoc_tenders.db` from https://huggingface.co/datasets/fireboy21/tenders_aoc
   - `targets.simple.csv` for `in_nse_debarred` and `worldbank_debarred` from https://data.opensanctions.org/datasets/latest/ (saved as `os_in_nse_debarred.csv`, `os_worldbank_debarred.csv`)
   - `GFR_2017_updated_31012026.pdf` from https://doe.gov.in/files/circulars_document/GFRupdatedupto31012026.pdf
2. Run `python3 scripts/build_gfr_clauses.py` and `python3 scripts/build_dataset.py`.

## Added (synthetic) fields

- `vendors.bank_account` is generated (prefix `DEMO`) only to show role-based masking. It is not a real account.
- `vendors.pan` is filled only where a real PAN is published on a debarment list; otherwise it is empty.

## Demo cases found in the real data

| Case | What the data shows | Used in |
|---|---|---|
| Teleconcepts (India) Pvt Ltd, ROC Hyderabad | Won a ₹48.49 lakh MES award (01-Apr-2023, 9 bids); the company registry now shows **Strike Off** | Scene 5: the status-change update |
| Global Enterprises | Won a ₹23.49 lakh MES Hyderabad award; a firm with the **same name** is on the NSE/SEBI debarred list (PAN AAJFG7931K), but no PAN links them | "No evidence, no answer": a name-only match is reported as *possible, needs review*, never as debarred |
| Single-bid awards | 1,384 of 11,889 awards received only one bid | Rule R8 review note |

A registry status or a name match on a list is a fact for review, not a finding of wrongdoing.
