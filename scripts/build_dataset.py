"""Build the small, real-data demo database for MAKOS.

Inputs (data/raw/, not committed):
  registered_companies.csv   MCA company master (CIN, name, status, ...)
  aoc_tenders.db             CPPP award-of-contract records (SQLite)
  os_in_nse_debarred.csv     NSE / SEBI debarred entities (via OpenSanctions)
  os_worldbank_debarred.csv  World Bank debarred firms (via OpenSanctions)

Output:
  data/processed/makos.db    vendors, awards, debarments (+ data dictionary)

What we keep: awards placed by organisations in Telangana / Andhra Pradesh
(2023 onwards) and the vendors who won them, matched by name to the company
registry and the debarment lists. Personal data (emails, personal addresses,
individuals on debarment lists) is dropped.
"""
import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
OUT = ROOT / "data/processed/makos.db"

REGION = re.compile(
    r"HYDERABAD|SECUNDERABAD|TELANGANA|RAMAGUNDAM|WARANGAL|KARIMNAGAR|NIZAMABAD|KHAMMAM|"
    r"VISAKHAPATNAM|VIZAG|ANDHRA|VIJAYAWADA|TIRUPATI|GUNTUR|KAKINADA|NELLORE|KURNOOL|SINGARENI",
    re.I,
)
STATUS_LABEL = {
    "ACTV": "Active", "STOF": "Strike Off", "UPSO": "Under process of Strike Off",
    "AMAL": "Amalgamated", "CLLP": "Converted to LLP", "DISD": "Dissolved",
    "NAEF": "Not available for e-filing", "ULQD": "Under Liquidation", "CLLD": "Converted to LLP and Dissolved",
    "D455": "Dormant under section 455", "LIQD": "Liquidated", "DRMT": "Dormant", "MLIQ": "Under Liquidation",
}

_SUFFIX = [
    (r"\bPRIVATE\b", "PVT"), (r"\bLIMITED\b", "LTD"), (r"\bCOMPANY\b", "CO"),
    (r"\bCORPORATION\b", "CORP"), (r"\bAND\b", "&"), (r"\bINDIA\b", "INDIA"),
]


def norm(name: str) -> str:
    s = (name or "").upper()
    s = re.sub(r"^\s*(M\s*/\s*S\.?|MS\.?|MESSRS\.?)\s+", "", s)
    s = s.replace("&AMP;", "&")
    for pat, rep in _SUFFIX:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^A-Z0-9& ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_value(v: str):
    try:
        x = float(str(v).replace(",", "").strip())
        return x if x > 0 else None
    except ValueError:
        return None


def load_awards():
    src = sqlite3.connect(f"file:{RAW / 'aoc_tenders.db'}?mode=ro", uri=True)
    q = """
      SELECT t.internal_id, t.year, t.org_name, t.title, t.tender_id, t.detail_url, d.details_json
      FROM aoc_tenders t JOIN aoc_details d ON d.internal_id = t.internal_id
      WHERE t.year >= 2023
    """
    rows = []
    for iid, year, org, title, tid, url, js in src.execute(q):
        try:
            d = json.loads(js)
        except (TypeError, ValueError):
            continue
        org_full = d.get("Organisation Name") or org or ""
        if not REGION.search(org_full + " " + (org or "")):
            continue
        bidder = (d.get("Name of the selected bidder(s)") or "").strip()
        value = parse_value(d.get("Contract Value"))
        if not bidder or value is None or "TEST" in (title or "").upper():
            continue
        bids = d.get("Number of bids received")
        rows.append({
            "award_id": iid, "year": year, "org_name": org_full.strip(), "state_or_portal": org,
            "title": (d.get("Tender Description") or title or "").strip(),
            "tender_type": d.get("Tender Type"), "tender_id": tid,
            "contract_value": value, "bids_received": int(bids) if str(bids).isdigit() else None,
            "bidder_name": bidder, "contract_date": d.get("Contract Date"),
            "published_date": d.get("Published Date"), "source_url": url,
        })
    return rows


def load_mca(wanted: set):
    """Return {norm_name: company} for names we need (reads the 593 MB file once)."""
    hits = {}
    with open(RAW / "registered_companies.csv", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            k = norm(r["COMPANY_NAME"])
            if k in wanted and (k not in hits or r["COMPANY_STATUS"] == "ACTV"):
                hits[k] = {
                    "cin": r["CORPORATE_IDENTIFICATION_NUMBER"].strip(),
                    "mca_name": r["COMPANY_NAME"].strip(),
                    "mca_status": r["COMPANY_STATUS"].strip(),
                    "company_class": r["COMPANY_CLASS"].strip(),
                    "date_of_registration": r["DATE_OF_REGISTRATION"].strip(),
                    "registered_state": r["REGISTERED_STATE"].strip(),
                    "authorized_capital": parse_value(r["AUTHORIZED_CAP"]),
                    "paidup_capital": parse_value(r["PAIDUP_CAPITAL"]),
                    "activity": r["PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN"].strip(),
                    "roc": r["REGISTRAR_OF_COMPANIES"].strip(),
                    "latest_annual_return": r["LATEST_YEAR_ANNUAL_RETURN"].strip(),
                }
    return hits


def load_debarments():
    out = []
    for r in csv.DictReader(open(RAW / "os_in_nse_debarred.csv", encoding="utf-8")):
        pans = [p for p in r["identifiers"].split(";") if re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p)]
        # Keep firms only: the 4th PAN character is 'C' for companies (also F/T/A for firms/trusts/AOP).
        if not pans or pans[0][3] not in "CFTA":
            continue
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", r["sanctions"])
        out.append({"name": r["name"].strip(), "list_name": "NSE / SEBI debarred entities", "pan": pans[0],
                    "grounds": "Debarred by SEBI / competent authority order", "start_date": dates[0] if dates else None,
                    "end_date": None, "country": "in"})
    for r in csv.DictReader(open(RAW / "os_worldbank_debarred.csv", encoding="utf-8")):
        if "in" not in r["countries"].split(";") or re.match(r"^(MR|MRS|DR)\.?\s", r["name"].upper()):
            continue
        m = re.search(r"^(.*?) - (\d{4}-\d{2}-\d{2}) - (\d{4}-\d{2}-\d{2})", r["sanctions"])
        out.append({"name": r["name"].strip(), "list_name": "World Bank debarred firms", "pan": None,
                    "grounds": m.group(1).strip() if m else r["sanctions"][:120],
                    "start_date": m.group(2) if m else None,
                    "end_date": (None if not m or m.group(3).startswith("2999") else m.group(3)), "country": "in"})
    return out


SCHEMA = """
CREATE TABLE vendors (
  vendor_id INTEGER PRIMARY KEY, name TEXT, norm_name TEXT UNIQUE,
  cin TEXT, mca_name TEXT, mca_status TEXT, mca_status_label TEXT, company_class TEXT,
  date_of_registration TEXT, registered_state TEXT, authorized_capital REAL, paidup_capital REAL,
  activity TEXT, roc TEXT, latest_annual_return TEXT,
  pan TEXT, pan_is_synthetic INTEGER DEFAULT 0, bank_account TEXT, bank_is_synthetic INTEGER DEFAULT 1,
  row_version INTEGER DEFAULT 1, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE awards (
  award_id TEXT PRIMARY KEY, vendor_id INTEGER REFERENCES vendors(vendor_id), year INTEGER,
  org_name TEXT, state_or_portal TEXT, title TEXT, tender_type TEXT, tender_id TEXT,
  contract_value REAL, bids_received INTEGER, bidder_name TEXT,
  contract_date TEXT, published_date TEXT, source_url TEXT, row_version INTEGER DEFAULT 1
);
CREATE TABLE debarments (
  debarment_id INTEGER PRIMARY KEY, vendor_id INTEGER REFERENCES vendors(vendor_id),
  name TEXT, norm_name TEXT, list_name TEXT, pan TEXT, grounds TEXT, start_date TEXT, end_date TEXT,
  row_version INTEGER DEFAULT 1
);
CREATE INDEX idx_awards_vendor ON awards(vendor_id);
CREATE INDEX idx_deb_norm ON debarments(norm_name);
CREATE TABLE data_dictionary (table_name TEXT, column_name TEXT, source TEXT, note TEXT);
"""


def synthetic_bank(cin_or_name: str) -> str:
    # Clearly synthetic, deterministic, for the masking demo only (not a real account).
    h = int(hashlib.sha1(cin_or_name.encode()).hexdigest(), 16) % 10**10
    return f"DEMO{h:010d}"


def main() -> None:
    awards = load_awards()
    print(f"region awards (2023+): {len(awards)}")
    debar = load_debarments()
    print(f"debarred firms kept: {len(debar)}")
    wanted = {norm(a["bidder_name"]) for a in awards} | {norm(d["name"]) for d in debar}
    mca = load_mca(wanted)
    print(f"registry matches: {len(mca)} of {len(wanted)} names")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript(SCHEMA)

    vid = {}

    def vendor_for(name: str) -> int:
        # A PAN seen on a debarment list is NEVER copied onto a vendor matched only by name:
        # a name match is not proof that it is the same firm.
        k = norm(name)
        if k in vid:
            return vid[k]
        m = mca.get(k, {})
        cur = db.execute(
            """INSERT INTO vendors(name, norm_name, cin, mca_name, mca_status, mca_status_label, company_class,
               date_of_registration, registered_state, authorized_capital, paidup_capital, activity, roc,
               latest_annual_return, pan, pan_is_synthetic, bank_account)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name.strip(), k, m.get("cin"), m.get("mca_name"), m.get("mca_status"),
             STATUS_LABEL.get(m.get("mca_status"), m.get("mca_status")), m.get("company_class"),
             m.get("date_of_registration"), m.get("registered_state"), m.get("authorized_capital"),
             m.get("paidup_capital"), m.get("activity"), m.get("roc"), m.get("latest_annual_return"),
             None, 0, synthetic_bank(m.get("cin") or k)))
        vid[k] = cur.lastrowid
        return vid[k]

    for a in awards:
        v = vendor_for(a["bidder_name"])
        db.execute("""INSERT OR IGNORE INTO awards(award_id, vendor_id, year, org_name, state_or_portal, title,
                      tender_type, tender_id, contract_value, bids_received, bidder_name, contract_date,
                      published_date, source_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (a["award_id"], v, a["year"], a["org_name"], a["state_or_portal"], a["title"], a["tender_type"],
                    a["tender_id"], a["contract_value"], a["bids_received"], a["bidder_name"], a["contract_date"],
                    a["published_date"], a["source_url"]))
    award_vendors = set(vid)
    for d in debar:
        k = norm(d["name"])
        v = vendor_for(d["name"]) if (k in award_vendors or k in mca) else None
        db.execute("""INSERT INTO debarments(vendor_id, name, norm_name, list_name, pan, grounds, start_date, end_date)
                      VALUES(?,?,?,?,?,?,?,?)""",
                   (v, d["name"], k, d["list_name"], d["pan"], d["grounds"], d["start_date"], d["end_date"]))

    dd = [
        ("vendors", "cin, mca_*, date_of_registration, capital, activity", "MCA company master (data.gov.in)", "real"),
        ("vendors", "pan", "none", "empty: no public source links a PAN to these vendors; list PANs stay on the debarments table"),
        ("vendors", "bank_account", "generated", "SYNTHETIC (DEMO prefix) for the masking demo only"),
        ("awards", "*", "CPPP award-of-contract records (eprocure.gov.in)", "real, as published"),
        ("debarments", "*", "NSE/SEBI and World Bank lists (via OpenSanctions)", "real; individuals removed"),
    ]
    db.executemany("INSERT INTO data_dictionary VALUES(?,?,?,?)", dd)
    db.commit()
    for t in ("vendors", "awards", "debarments"):
        print(t, db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    db.close()


if __name__ == "__main__":
    main()
