"""Split the official GFR 2017 PDF (updated to 31.01.2026) into rule-level clauses.

Input : data/raw/GFR_2017_updated_31012026.pdf  (downloaded from doe.gov.in)
Output: data/processed/gfr_clauses.json

Each clause keeps its rule number, title, cleaned text, the page it starts on and
the amendment notes (e.g. "Amended vide DoE OM No. F.1/3/2024-PPD dated 10.07.2024")
found in the footnotes near it, so answers can cite an exact rule and version.
"""
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data/raw/GFR_2017_updated_31012026.pdf"
OUT = ROOT / "data/processed/gfr_clauses.json"

# Chapter 6 (procurement of goods and services) is the part MAKOS uses.
FIRST_RULE, LAST_RULE = 142, 178

HEAD = re.compile(r"^Rule\s+(\d{3})(?:\s*\(\d\))?\.?\s+(.*)$")
FOOTNOTE = re.compile(r"^\d{1,3}\s+(Amended|Inserted|Deleted|Substituted|Replace|Added)\b.*", re.I)
OM_DATE = re.compile(r"dated\s+(\d{2}\.\d{2}\.\d{4})")


def clean(text: str) -> str:
    text = re.sub(r"\[|\]|\{|\}", "", text)          # drop amendment brackets
    # drop footnote markers glued to a word or bracket, e.g. "lakh)27", "only15", "-)4"
    text = re.sub(r"(?<=[A-Za-z)\-/])\d{1,4}(?=[\s.,;:)]|$)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    if not PDF.exists():
        sys.exit(f"missing {PDF}; download it from doe.gov.in first")
    doc = pymupdf.open(PDF)
    lines = []  # (page_no, line)
    for pno, page in enumerate(doc, start=1):
        for line in page.get_text().splitlines():
            lines.append((pno, line.strip()))

    clauses, cur = [], None
    for pno, line in lines:
        m = HEAD.match(line)
        if m and FIRST_RULE <= int(m.group(1)) <= LAST_RULE + 1 and not line.startswith("Rule 154 and"):
            if cur:
                clauses.append(cur)
            num = int(m.group(1))
            if num > LAST_RULE:
                cur = None
                break
            cur = {"rule": num, "title": m.group(2), "page": pno, "body": [], "notes": []}
            continue
        if cur is None:
            continue
        if FOOTNOTE.match(line):
            cur["notes"].append(line)
        else:
            cur["body"].append(line)
    if cur:
        clauses.append(cur)

    # Merge duplicate headings for the same rule (e.g. Rule 172 (1) / (2), Rule 175 (1) / (2)).
    merged: dict[int, dict] = {}
    for c in clauses:
        m = merged.setdefault(c["rule"], {"rule": c["rule"], "title": c["title"], "page": c["page"], "body": [], "notes": []})
        m["body"] += c["body"]
        m["notes"] += c["notes"]

    out = []
    for c in sorted(merged.values(), key=lambda x: x["rule"]):
        text = clean(c["title"] + " " + " ".join(c["body"]))
        dates = sorted({d for n in c["notes"] for d in OM_DATE.findall(n)}, key=lambda d: d[6:] + d[3:5] + d[:2])
        out.append({
            "clause_id": f"GFR-{c['rule']}",
            "rule": c["rule"],
            "title": text.split(".")[0][:90] if len(text.split(".")[0]) < 90 else clean(c["title"]),
            "page": c["page"],
            "text": text,
            "amendment_notes": [clean(n) for n in c["notes"]],
            "last_amended": dates[-1] if dates else None,
            "source": "General Financial Rules 2017 (updated up to 31.01.2026), Department of Expenditure, Ministry of Finance",
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {len(out)} clauses to {OUT}")


if __name__ == "__main__":
    main()
