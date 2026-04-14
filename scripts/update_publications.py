#!/usr/bin/env python3
"""
Update publications from Google Scholar to _bibliography/papers.bib

Usage:
    pip install scholarly bibtexparser
    python scripts/update_publications.py

The script:
  1. Fetches all publications from the configured Google Scholar profile.
  2. Converts each publication to a BibTeX entry.
  3. Merges with the existing papers.bib:
       - Existing entries are kept intact (custom fields like abbr, selected,
         bibtex_show, html are preserved).
       - New entries not yet in the file are appended.
  4. Writes the result back to _bibliography/papers.bib.
"""

import re
import sys
import time
import logging
from pathlib import Path

try:
    from scholarly import scholarly, ProxyGenerator
except ImportError:
    sys.exit("scholarly not installed. Run: pip install scholarly")

try:
    import bibtexparser
    from bibtexparser.bwriter import BibTexWriter
    from bibtexparser.bparser import BibTexParser
    from bibtexparser.customization import convert_to_unicode
except ImportError:
    sys.exit("bibtexparser not installed. Run: pip install bibtexparser")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCHOLAR_USER_ID = "6xUhlnwAAAAJ"
BIB_FILE = Path(__file__).parent.parent / "_bibliography" / "papers.bib"

# Default custom fields added to every NEW entry
DEFAULT_BIBTEX_SHOW = True

# Optional: set to True to route requests through a free ScraperAPI proxy
# (helps avoid rate-limiting / CAPTCHAs). Requires `pip install free-proxy`
USE_FREE_PROXY = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convert text to a safe ASCII slug for use in BibTeX keys."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:40]


def make_bibtex_key(pub: dict) -> str:
    """Generate a deterministic BibTeX key from publication metadata."""
    bib = pub.get("bib", {})
    first_author = bib.get("author", "unknown").split(" and ")[0]
    # Try to get last name
    parts = first_author.strip().split()
    last_name = parts[-1] if parts else "unknown"
    year = str(bib.get("pub_year", "0000"))
    title_words = bib.get("title", "").split()
    first_title_word = slugify(title_words[0]) if title_words else "untitled"
    return f"{slugify(last_name)}{year}_{first_title_word}"


def pub_to_bibtex_entry(pub: dict) -> dict:
    """
    Convert a scholarly publication dict to a bibtexparser entry dict.
    Returns None if the publication lacks enough data.
    """
    bib = pub.get("bib", {})
    title = bib.get("title", "").strip()
    if not title:
        return None

    # Determine entry type
    venue = bib.get("venue", "")
    pub_type = bib.get("pub_type", "")
    if pub_type == "patent":
        entry_type = "misc"
    elif "journal" in pub_type.lower() or "arxiv" in venue.lower():
        entry_type = "article"
    else:
        entry_type = "inproceedings"

    key = make_bibtex_key(pub)

    entry = {
        "ENTRYTYPE": entry_type,
        "ID": key,
        "title": title,
        "author": bib.get("author", ""),
        "year": str(bib.get("pub_year", "")),
        "bibtex_show": "true",
    }

    if entry_type == "article":
        if bib.get("journal"):
            entry["journal"] = bib["journal"]
        if bib.get("volume"):
            entry["volume"] = str(bib["volume"])
        if bib.get("number"):
            entry["number"] = str(bib["number"])
        if bib.get("pages"):
            entry["pages"] = bib["pages"]
    elif entry_type == "inproceedings":
        if bib.get("conference"):
            entry["booktitle"] = bib["conference"]
        elif venue:
            entry["booktitle"] = venue
        if bib.get("pages"):
            entry["pages"] = bib["pages"]
    elif entry_type == "misc":
        if bib.get("abstract"):
            entry["note"] = bib["abstract"][:200]

    # Links
    eprint_url = pub.get("eprint_url", "")
    pub_url = pub.get("pub_url", "")
    url = eprint_url or pub_url
    if url:
        entry["html"] = url

    # Citation count as a note (informational)
    if pub.get("num_citations"):
        entry["note"] = f"Citations: {pub['num_citations']}"

    return entry


def load_bib(path: Path) -> bibtexparser.bibdatabase.BibDatabase:
    """Load an existing .bib file (returns empty DB if file missing)."""
    if not path.exists():
        return bibtexparser.bibdatabase.BibDatabase()
    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    with open(path, encoding="utf-8") as f:
        return bibtexparser.load(f, parser=parser)


def write_bib(db: bibtexparser.bibdatabase.BibDatabase, path: Path) -> None:
    """Write BibDatabase to file, preserving custom Jekyll/al-folio fields."""
    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False
    # Keep custom fields like bibtex_show, abbr, selected, html intact
    writer.display_order = [
        "bibtex_show",
        "abbr",
        "title",
        "author",
        "year",
        "booktitle",
        "journal",
        "volume",
        "number",
        "pages",
        "publisher",
        "organization",
        "doi",
        "html",
        "pdf",
        "selected",
        "note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(writer.write(db))
    log.info("Wrote %d entries to %s", len(db.entries), path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if USE_FREE_PROXY:
        log.info("Setting up free proxy to avoid rate-limiting...")
        pg = ProxyGenerator()
        pg.FreeProxies()
        scholarly.use_proxy(pg)

    log.info("Fetching author profile for user ID: %s", SCHOLAR_USER_ID)
    try:
        author = scholarly.search_author_id(SCHOLAR_USER_ID)
        author = scholarly.fill(author, sections=["publications"])
    except Exception as exc:
        sys.exit(f"Failed to fetch Google Scholar profile: {exc}")

    pubs = author.get("publications", [])
    log.info("Found %d publications on Google Scholar", len(pubs))

    # Load existing bib file
    db = load_bib(BIB_FILE)
    def _norm_title(t: str) -> str:
        """Normalise title for deduplication: lowercase, strip punctuation/spaces."""
        return re.sub(r"[^a-z0-9]+", "", t.lower())

    existing_titles = {_norm_title(e.get("title", "")) for e in db.entries}
    existing_keys = {e["ID"] for e in db.entries}

    added = 0
    skipped = 0

    for idx, pub in enumerate(pubs):
        # Fill individual publication details
        try:
            pub = scholarly.fill(pub)
        except Exception as exc:
            log.warning("Could not fill publication %d: %s", idx, exc)

        entry = pub_to_bibtex_entry(pub)
        if entry is None:
            skipped += 1
            continue

        title_lower = _norm_title(entry.get("title", ""))

        # Skip if already present (match by title)
        if title_lower in existing_titles:
            log.debug("Already exists (title match): %s", entry["title"])
            skipped += 1
            continue

        # Deduplicate key if needed
        key = entry["ID"]
        if key in existing_keys:
            key = f"{key}_{added}"
            entry["ID"] = key
        existing_keys.add(key)

        db.entries.append(entry)
        existing_titles.add(_norm_title(entry["title"]))
        added += 1
        log.info("[+] New entry: %s (%s)", entry["title"], entry["year"])

        # Be polite to Google Scholar
        time.sleep(1)

    log.info("Summary: %d new, %d skipped (already present or no data)", added, skipped)

    if added > 0:
        write_bib(db, BIB_FILE)
        log.info("papers.bib updated successfully.")
    else:
        log.info("No new publications found. papers.bib unchanged.")


if __name__ == "__main__":
    main()
