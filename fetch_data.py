#!/usr/bin/env python3
"""
Startup Funding Dashboard — data pipeline
=========================================
Fetches global startup funding news, enriches each event with:
  - problem category (Professional excellence, Time freedom, ... 18 total)
  - sector / product focus area (Fintech, Robotics, HealthTech, ...)
  - country of origin + region
  - stage type (new / emerging / established)
  - research-based flag, AI flag
and MERGES into data/companies.json — an accumulating dataset.
Companies seen in multiple funding events become "regularly funded".

Run daily via GitHub Actions; the data file is committed back to the repo
and served by GitHub Pages alongside index.html.

Env:
  LOOKBACK_HOURS  default 26 (slight overlap so nothing is missed)
"""

import json
import os
import re
import html
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import mktime

import feedparser
import requests

DATA_PATH = Path(__file__).parent / "data" / "companies.json"
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "26"))

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

FEEDS = {
    "TechCrunch Venture": ("https://techcrunch.com/category/venture/feed/", "North America"),
    "Crunchbase News":    ("https://news.crunchbase.com/feed/", "Global"),
    "Pulse 2.0":          ("https://pulse2.com/feed/", "North America"),
    "Tech Funding News":  ("https://techfundingnews.com/feed/", "Global"),
    "EU-Startups":        ("https://www.eu-startups.com/feed/", "Europe"),
    "Tech.eu":            ("https://tech.eu/feed/", "Europe"),
    "ArcticStartup":      ("https://arcticstartup.com/feed/", "Europe"),
    "Inc42":              ("https://inc42.com/feed/", "Asia"),
    "BetaKit":            ("https://betakit.com/feed/", "North America"),
    "Startup Daily":      ("https://www.startupdaily.net/feed/", "Oceania"),
}

# ---------------------------------------------------------------- filters

FUNDING_PATTERNS = re.compile(
    r"\b(raises?|raised|secures?|secured|closes?|closed|lands?|landed|"
    r"bags?|bagged|nabs?|nabbed|snags?|scores?|attracts?|gets?|wins?)\b"
    r".{0,80}?"
    r"((\$|€|£|₹|US\$|USD|EUR|GBP|INR|A\$|C\$|CHF|SEK|NOK)\s?\d|"
    r"\d+(\.\d+)?\s?(million|billion|mn|bn|m\b|b\b|crore|cr\b|lakh))"
    r"|\b(funding round|seed round|pre-seed|series\s+[a-h]\b|"
    r"venture round|equity round|bridge round)\b",
    re.IGNORECASE)

EXCLUDE_PATTERNS = re.compile(
    r"\b(how to|best \d+|top \d+|\d+ best|guide to|review:|opinion:|"
    r"offloads? shares|sells? stake|ipo window|layoffs?|shuts? down|"
    r"weekly recap|roundup|newsletter|sector snapshot|funding rebounds?|"
    r"\d+ companies|funding (report|trends|data)|state of|biggest funding|week’?s \d+|this week)\b",
    re.IGNORECASE)

MONEY_RE = re.compile(
    r"((\$|€|£|₹|US\$|A\$|C\$)\s?\d+(?:[.,]\d+)?\s?"
    r"(?:million|billion|mn|bn|m\b|b\b|k\b)?"
    r"|\d+(?:[.,]\d+)?\s?(?:million|billion|mn|bn|crore|cr)\b"
    r"(?:\s?(?:dollars|euros|pounds|rupees|usd|eur|gbp|inr))?)",
    re.IGNORECASE)

ROUND_RE = re.compile(
    r"\b(pre-?seed|seed|series\s+[a-h]\d?|angel|bridge|growth|"
    r"venture\s+round|debt|grant|extension|pre-?series\s+[a-h])\b",
    re.IGNORECASE)

AI_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml\b|deep learning|"
    r"llm|large language model|genai|generative ai|gen ai|neural|gpt|"
    r"computer vision|nlp|natural language|agentic|ai agent|autonomous agent|"
    r"foundation model|copilot|chatbot|speech recognition|robotics?\b|"
    r"ai-powered|ai-driven|ai-native|ai-first)\b", re.IGNORECASE)

RESEARCH_RE = re.compile(
    r"\b(spin-?out|spin-?off|university|professor|ph\.?d|research lab|"
    r"institute|deep[- ]tech|quantum|peer-reviewed|patent(ed)?|"
    r"breakthrough research|scientists?|researchers?|r&d|clinical)\b",
    re.IGNORECASE)

# ------------------------------------------------- problem categories
# First match wins — ordered most-specific to most-general.

CATEGORIES = [
    ("Health & longevity",
     r"health|medical|medtech|biotech|pharma|diagnos|clinic|patient|cancer|"
     r"therap|drug|disease|hospital|longevity|fitness|wellness|mental health|"
     r"paediatric|pediatric|dental|surgery|vaccine|genomic"),
    ("Climate & sustainability",
     r"climate|carbon|solar|wind energy|battery|renewable|sustainab|"
     r"recycl|circular|emission|green energy|clean ?tech|ev charg|geothermal|"
     r"hydrogen|biofuel|weather tech|environmental"),
    ("Security & trust",
     r"cyber|security|fraud|identity verification|privacy|compliance|"
     r"encryption|threat|malware|authentication|zero trust|kyc|aml"),
    ("Financial independence",
     r"fintech|payment|banking|investing app|investment platform|wealth|trading platform|savings|lending|"
     r"insurance|insurtech|credit|crypto|stablecoin|token|remittance|"
     r"mortgage|neobank|challenger bank|treasury"),
    ("Scientific discovery",
     r"quantum|space ?tech|satellite|materials science|semiconductor research|"
     r"fusion|biolog(y|ical) research|laborator|telescope|particle|genome"),
    ("Mobility & logistics",
     r"logistics|supply chain|delivery|freight|shipping|mobility|"
     r"automotive|vehicle|drone deliver|fleet|transport|rail|maritime|"
     r"last[- ]mile|warehouse"),
    ("Food & agriculture",
     r"food ?tech|agri|farm|crop|restaurant|grocer|meal|nutrition|"
     r"beverage|chocolate|dairy|aquaculture|vertical farm"),
    ("Learning & growth",
     r"edtech|education|learning|upskill|training platform|tutoring|"
     r"course|curriculum|school|university platform|certification"),
    ("Creative expression",
     r"design tool|music|video creation|content creation|creator|"
     r"photo|film|animation|art platform|writing tool|podcast|fashion design"),
    ("Entertainment & play",
     r"gaming|game studio|esports|streaming|sports ?tech|entertainment|"
     r"betting|fantasy|trading card|toys|leisure|travel|tourism|hospitality"),
    ("Connection & community",
     r"social network|dating|community platform|messaging|communication|"
     r"events platform|networking app|forum"),
    ("Time freedom",
     r"automat|ai agent|agentic|autonomous|no-?code|low-?code|copilot|"
     r"assistant|workflow automation|rpa|orchestrat"),
    ("Infrastructure & tools",
     r"cloud|data infra|database|api |developer|devops|observability|"
     r"semiconductor|chip|networking|data center|datacenter|compute|"
     r"open[- ]source|kubernetes|llm infra|model training|inference"),
    ("Entrepreneurship",
     r"smb|small business|e-?commerce|marketplace platform|seller|"
     r"merchant|fundraising platform|startup tool|shopify|d2c|"
     r"brand|retail tech|cap table"),
    ("Professional excellence",
     r"enterprise|b2b|saas|productivity|sales|marketing|crm|hr ?tech|"
     r"recruit|hiring|talent|legal ?tech|accounting|procurement|"
     r"analytics|business intelligence|customer (support|service)|erp"),
    ("Societal transformation",
     r"govtech|government|civic|accessibility|nonprofit|social impact|"
     r"financial inclusion|refugee|democracy|public sector|smart city"),
    ("Life management",
     r"household|family|parenting|personal assistant|scheduling|"
     r"home services|real estate|proptech|rental|personal finance app|"
     r"insurance app|chores|pet"),
    ("Personal transformation",
     r"coaching|mindfulness|meditation|self-?improvement|habit|"
     r"life coach|motivation|journaling|sleep|wellbeing"),
]
CATEGORIES = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in CATEGORIES]

# ------------------------------------------------- sectors (product focus)

SECTORS = [
    ("Robotics",        r"robot|humanoid|cobot|automation hardware"),
    ("Cybersecurity",   r"cyber|security platform|threat|malware|encryption"),
    ("Fintech",         r"fintech|payment|banking|lending|investing app|trading platform|"
                        r"insurtech|crypto|stablecoin|neobank|wealth"),
    ("HealthTech / Bio",r"health|medical|biotech|pharma|diagnos|clinical|medtech"),
    ("Climate / Energy",r"climate|energy|solar|battery|carbon|renewable|hydrogen"),
    ("AI Infrastructure",r"llm|foundation model|model training|inference|"
                        r"gpu|ai infra|vector database|ml ?ops"),
    ("DevTools / Cloud",r"developer|devops|api |cloud|database|observability|"
                        r"open[- ]source|kubernetes"),
    ("Semiconductors",  r"semiconductor|chip|fab|silicon"),
    ("SpaceTech",       r"space|satellite|orbital|launch vehicle"),
    ("Mobility / Logistics", r"logistics|supply chain|delivery|freight|mobility|"
                        r"automotive|fleet|transport"),
    ("FoodTech / AgTech", r"food|agri|farm|restaurant|grocer|beverage|chocolate"),
    ("EdTech",          r"edtech|education|learning|tutoring|upskill"),
    ("PropTech",        r"proptech|real estate|rental|construction|housing"),
    ("HRTech",          r"hr ?tech|recruit|hiring|talent|payroll|workforce"),
    ("LegalTech",       r"legal|law firm|contract management|compliance"),
    ("Gaming / Media",  r"gaming|game|esports|streaming|media|entertainment|"
                        r"creator|content"),
    ("E-commerce / Retail", r"e-?commerce|retail|marketplace|d2c|brand|seller|merchant"),
    ("Enterprise SaaS", r"saas|enterprise|b2b|crm|productivity|analytics|"
                        r"sales|marketing|workflow"),
    ("Consumer Apps",   r"consumer|app for|dating|social|lifestyle|personal"),
    ("Deep Tech",       r"quantum|fusion|materials|photonics|biocomput"),
]
SECTORS = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in SECTORS]

# ------------------------------------------------- geography

CITY_COUNTRY = {
    # North America
    "san francisco": "USA", "new york": "USA", "nyc": "USA", "boston": "USA",
    "austin": "USA", "seattle": "USA", "los angeles": "USA", "chicago": "USA",
    "miami": "USA", "denver": "USA", "palo alto": "USA", "mountain view": "USA",
    "san jose": "USA", "atlanta": "USA", "dallas": "USA", "houston": "USA",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada",
    "montréal": "Canada", "waterloo": "Canada", "ottawa": "Canada",
    "calgary": "Canada", "mexico city": "Mexico",
    # Europe
    "london": "UK", "cambridge": "UK", "oxford": "UK", "manchester": "UK",
    "edinburgh": "UK", "bristol": "UK", "paris": "France", "lyon": "France",
    "berlin": "Germany", "munich": "Germany", "hamburg": "Germany",
    "cologne": "Germany", "frankfurt": "Germany", "amsterdam": "Netherlands",
    "rotterdam": "Netherlands", "eindhoven": "Netherlands",
    "barcelona": "Spain", "madrid": "Spain", "valencia": "Spain",
    "milan": "Italy", "rome": "Italy", "turin": "Italy",
    "stockholm": "Sweden", "gothenburg": "Sweden", "oslo": "Norway",
    "copenhagen": "Denmark", "helsinki": "Finland", "espoo": "Finland",
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "lausanne": "Switzerland", "vienna": "Austria", "dublin": "Ireland",
    "lisbon": "Portugal", "porto": "Portugal", "brussels": "Belgium",
    "antwerp": "Belgium", "ghent": "Belgium", "warsaw": "Poland",
    "krakow": "Poland", "kraków": "Poland", "prague": "Czechia",
    "budapest": "Hungary", "bucharest": "Romania", "sofia": "Bulgaria",
    "athens": "Greece", "tallinn": "Estonia", "riga": "Latvia",
    "vilnius": "Lithuania", "zagreb": "Croatia", "luxembourg": "Luxembourg",
    "kyiv": "Ukraine", "istanbul": "Turkey",
    # Asia / MEA
    "bangalore": "India", "bengaluru": "India", "mumbai": "India",
    "delhi": "India", "new delhi": "India", "gurugram": "India",
    "gurgaon": "India", "noida": "India", "hyderabad": "India",
    "chennai": "India", "pune": "India", "singapore": "Singapore",
    "jakarta": "Indonesia", "tokyo": "Japan", "osaka": "Japan",
    "seoul": "South Korea", "beijing": "China", "shanghai": "China",
    "shenzhen": "China", "hangzhou": "China", "hong kong": "Hong Kong",
    "taipei": "Taiwan", "manila": "Philippines", "bangkok": "Thailand",
    "ho chi minh": "Vietnam", "hanoi": "Vietnam", "kuala lumpur": "Malaysia",
    "dubai": "UAE", "abu dhabi": "UAE", "riyadh": "Saudi Arabia",
    "tel aviv": "Israel", "jerusalem": "Israel", "cairo": "Egypt",
    "lagos": "Nigeria", "nairobi": "Kenya", "cape town": "South Africa",
    "johannesburg": "South Africa", "karachi": "Pakistan",
    # Oceania / LatAm
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia",
    "auckland": "New Zealand", "wellington": "New Zealand",
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "buenos aires": "Argentina", "santiago": "Chile", "bogota": "Colombia",
    "bogotá": "Colombia", "lima": "Peru",
}

DEMONYM_COUNTRY = {
    "american": "USA", "us-based": "USA", "canadian": "Canada",
    "british": "UK", "english": "UK", "scottish": "UK", "welsh": "UK",
    "french": "France", "german": "Germany", "dutch": "Netherlands",
    "spanish": "Spain", "italian": "Italy", "swedish": "Sweden",
    "norwegian": "Norway", "danish": "Denmark", "finnish": "Finland",
    "swiss": "Switzerland", "austrian": "Austria", "irish": "Ireland",
    "portuguese": "Portugal", "belgian": "Belgium", "polish": "Poland",
    "czech": "Czechia", "hungarian": "Hungary", "romanian": "Romania",
    "bulgarian": "Bulgaria", "greek": "Greece", "estonian": "Estonia",
    "latvian": "Latvia", "lithuanian": "Lithuania", "croatian": "Croatia",
    "ukrainian": "Ukraine", "turkish": "Turkey", "indian": "India",
    "singaporean": "Singapore", "indonesian": "Indonesia",
    "japanese": "Japan", "korean": "South Korea", "chinese": "China",
    "taiwanese": "Taiwan", "israeli": "Israel", "emirati": "UAE",
    "saudi": "Saudi Arabia", "egyptian": "Egypt", "nigerian": "Nigeria",
    "kenyan": "Kenya", "south african": "South Africa",
    "australian": "Australia", "kiwi": "New Zealand",
    "brazilian": "Brazil", "argentine": "Argentina", "chilean": "Chile",
    "colombian": "Colombia", "mexican": "Mexico", "pakistani": "Pakistan",
}

COUNTRY_NAMES = {c.lower(): c for c in set(CITY_COUNTRY.values()) | {
    "United States", "Netherlands", "New Zealand", "South Korea",
    "Saudi Arabia", "South Africa", "Hong Kong", "Czechia"}}
COUNTRY_NAMES.update({"usa": "USA", "u.s.": "USA", "uk": "UK", "u.k.": "UK",
                      "united states": "USA", "united kingdom": "UK",
                      "the netherlands": "Netherlands", "uae": "UAE"})

COUNTRY_REGION = {
    "USA": "North America", "Canada": "Canada", "Mexico": "Latin America",
    "UK": "Europe", "France": "Europe", "Germany": "Europe",
    "Netherlands": "Europe", "Spain": "Europe", "Italy": "Europe",
    "Sweden": "Europe", "Norway": "Europe", "Denmark": "Europe",
    "Finland": "Europe", "Switzerland": "Europe", "Austria": "Europe",
    "Ireland": "Europe", "Portugal": "Europe", "Belgium": "Europe",
    "Poland": "Europe", "Czechia": "Europe", "Hungary": "Europe",
    "Romania": "Europe", "Bulgaria": "Europe", "Greece": "Europe",
    "Estonia": "Europe", "Latvia": "Europe", "Lithuania": "Europe",
    "Croatia": "Europe", "Luxembourg": "Europe", "Ukraine": "Europe",
    "Turkey": "Europe", "India": "Asia", "Singapore": "Asia",
    "Indonesia": "Asia", "Japan": "Asia", "South Korea": "Asia",
    "China": "Asia", "Hong Kong": "Asia", "Taiwan": "Asia",
    "Philippines": "Asia", "Thailand": "Asia", "Vietnam": "Asia",
    "Malaysia": "Asia", "Pakistan": "Asia", "UAE": "Middle East",
    "Saudi Arabia": "Middle East", "Israel": "Middle East",
    "Egypt": "Africa", "Nigeria": "Africa", "Kenya": "Africa",
    "South Africa": "Africa", "Australia": "Oceania",
    "New Zealand": "Oceania", "Brazil": "Latin America",
    "Argentina": "Latin America", "Chile": "Latin America",
    "Colombia": "Latin America", "Peru": "Latin America",
}
COUNTRY_REGION["Canada"] = "North America"

# ---------------------------------------------------------------- helpers

def clean_text(raw: str) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(raw or "")).strip()


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60]


def classify(items, text, default):
    for name, rx in items:
        if rx.search(text):
            return name
    return default


def extract_country(text: str, source_region: str) -> tuple[str, str]:
    low = text.lower()
    # "X-based" / "X-headquartered"
    for m in re.finditer(r"([\w\s'’.-]{2,30}?)[-\s](?:based|headquartered)", low):
        loc = m.group(1).strip().split(",")[-1].strip()
        if loc in CITY_COUNTRY:
            c = CITY_COUNTRY[loc]
            return c, COUNTRY_REGION.get(c, source_region)
        if loc in COUNTRY_NAMES:
            c = COUNTRY_NAMES[loc]
            return c, COUNTRY_REGION.get(c, source_region)
    # "Amsterdam's OurMind" possessive
    for m in re.finditer(r"\b([\w\s'’.-]{3,20})['’]s\s+[A-Z]", text):
        loc = m.group(1).strip().lower()
        if loc in CITY_COUNTRY:
            c = CITY_COUNTRY[loc]
            return c, COUNTRY_REGION.get(c, source_region)
    # demonyms: "Italian fintech"
    for dem, c in DEMONYM_COUNTRY.items():
        if re.search(rf"\b{dem}\b", low):
            return c, COUNTRY_REGION.get(c, source_region)
    # bare city mention
    for city, c in CITY_COUNTRY.items():
        if re.search(rf"\b{re.escape(city)}\b", low):
            return c, COUNTRY_REGION.get(c, source_region)
    # currency hints
    if "₹" in text or re.search(r"\b(crore|lakh)\b", low):
        return "India", "Asia"
    if "c$" in low:
        return "Canada", "North America"
    if "a$" in low:
        return "Australia", "Oceania"
    return "Unknown", source_region


def stage_type(round_name: str, amount: str, text: str) -> str:
    r = (round_name or "").lower()
    if re.search(r"pre-?seed|seed|angel", r):
        return "new"
    if re.search(r"series\s+[ab]\b|pre-?series", r):
        return "emerging"
    if re.search(r"series\s+[c-h]|growth|debt", r) or \
       re.search(r"unicorn|valuation of \$\d+ ?b", text, re.IGNORECASE):
        return "established"
    if re.search(r"\b(billion|bn)\b|\$\d{3,}\s?(m|million)", amount or "", re.IGNORECASE):
        return "established"
    return "unspecified"


def extract_company(title: str) -> str:
    m = re.split(
        r"\b(raises?|raised|secures?|secured|closes?|closed|lands?|landed|"
        r"bags?|bagged|nabs?|nabbed|snags?|scores?|attracts?|gets?|wins?|"
        r"nets?|netted)\b",
        title, maxsplit=1, flags=re.IGNORECASE)
    candidate = m[0].strip(" ,-–—:") if m else title
    candidate = re.sub(r"^(exclusive|breaking|report)\s*[:\-]\s*", "", candidate,
                       flags=re.IGNORECASE)
    candidate = re.sub(r"^[\w\s,'’.-]*[-\s]based\s+", "", candidate)
    candidate = re.sub(
        r"^(the\s+)?[\w\s'’.-]{0,20}?(startup|fintech|company|outfit|platform|"
        r"firm|app|unicorn|scale-?up|maker|brand|provider)\s+", "",
        candidate, flags=re.IGNORECASE)
    return (candidate.strip() or title)[:60]


# ---------------------------------------------------------------- pipeline

def fetch_events() -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    out, seen = [], set()
    for source, (url, region) in FEEDS.items():
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {source}: {exc}", file=sys.stderr)
            continue

        for e in entries:
            t = e.get("published_parsed") or e.get("updated_parsed")
            if not t:
                continue
            published = datetime.fromtimestamp(mktime(t), tz=timezone.utc)
            if published < cutoff:
                continue

            title = clean_text(e.get("title", ""))
            summary = clean_text(e.get("summary", ""))[:500]
            text = f"{title}. {summary}"
            if EXCLUDE_PATTERNS.search(title) or not FUNDING_PATTERNS.search(text):
                continue

            link = e.get("link", "")
            key = re.sub(r"\W+", "", title.lower())[:80]
            if link in seen or key in seen:
                continue
            seen.update((link, key))

            money = MONEY_RE.search(text)
            rnd = ROUND_RE.search(text)
            amount = money.group(0).strip() if money else ""
            round_name = rnd.group(0).title() if rnd else ""
            country, reg = extract_country(text, region)

            out.append({
                "company": extract_company(title),
                "title": title,
                "summary": summary[:280],
                "link": link,
                "source": source,
                "date": published.strftime("%Y-%m-%d"),
                "amount": amount,
                "round": round_name,
                "country": country,
                "region": reg,
                "category": classify(CATEGORIES, text, "Professional excellence"),
                "sector": classify(SECTORS, text, "Other"),
                "is_ai": bool(AI_RE.search(text)),
                "research_based": bool(RESEARCH_RE.search(text)),
                "stage": stage_type(round_name, amount, text),
            })
    return out


def merge(events: list[dict]) -> dict:
    if DATA_PATH.exists():
        db = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    else:
        db = {"updated": "", "companies": {}}

    companies = db["companies"]
    for ev in events:
        cid = slugify(ev["company"])
        if not cid:
            continue
        rec = companies.get(cid)
        event_entry = {k: ev[k] for k in
                       ("date", "amount", "round", "title", "link", "source")}
        if rec is None:
            companies[cid] = {
                "name": ev["company"],
                "country": ev["country"], "region": ev["region"],
                "category": ev["category"], "sector": ev["sector"],
                "is_ai": ev["is_ai"], "research_based": ev["research_based"],
                "stage": ev["stage"], "summary": ev["summary"],
                "first_seen": ev["date"], "last_seen": ev["date"],
                "events": [event_entry],
            }
        else:
            if not any(x["link"] == ev["link"] or x["title"] == ev["title"]
                       for x in rec["events"]):
                rec["events"].append(event_entry)
            rec["last_seen"] = max(rec["last_seen"], ev["date"])
            rec["is_ai"] = rec["is_ai"] or ev["is_ai"]
            rec["research_based"] = rec["research_based"] or ev["research_based"]
            if rec["country"] == "Unknown" and ev["country"] != "Unknown":
                rec["country"], rec["region"] = ev["country"], ev["region"]
            if ev["stage"] != "unspecified":
                rec["stage"] = ev["stage"]
            rec["summary"] = ev["summary"] or rec["summary"]

    db["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return db


def main() -> None:
    events = fetch_events()
    db = merge(events)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(db, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    n = len(db["companies"])
    multi = sum(1 for c in db["companies"].values() if len(c["events"]) > 1)
    print(f"[ok] {len(events)} fresh events | {n} companies tracked | "
          f"{multi} regularly funded")


if __name__ == "__main__":
    main()
