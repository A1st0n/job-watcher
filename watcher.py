#!/usr/bin/env python3
"""Check company job boards + SimplifyJobs every run; email new early-career SWE-type postings.

Run with --dry-run to print instead of email. State lives next to this file.
"""
import json, os, re, smtplib, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANIES = HERE / "companies.txt"  # the startup routine pushes new companies here
BOARDS, SEEN, LOG = HERE / "boards.json", HERE / "seen.json", HERE / "new_jobs.md"
SEEDED, OPENINGS = HERE / "seeded.json", HERE / "current_openings.md"
ME = os.environ.get("GMAIL_ADDRESS", "")  # GitHub secret; kept out of the public code
SIMPLIFY = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
SIMPLIFY_NG = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"

ROLE = re.compile(r"software|\bswe\b|\bsde\b|developer|\bsre\b|site reliability|devops|platform|infrastructure|"
                  r"embedded|firmware|robotics|back-?end|front-?end|full[- ]?stack|systems engineer|"
                  r"machine learning engineer|\bml engineer", re.I)
LEVEL = re.compile(r"\bintern(ship)?\b|co-?op|new grad|graduate|early career|entry[- ]level|university|"
                   r"\bjunior\b|associate software|engineer i\b|\b202[6-8]\b", re.I)
SENIOR = re.compile(r"senior|\bsr\.?\b|staff|principal|manager|director|\blead\b|\bphd\b", re.I)


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "job-watcher"}), timeout=20) as r:
            return json.load(r)
    except Exception:
        return None


def slugs(name):
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    out = ["".join(base), "-".join(base)]
    if len(base) > 1 and base[-1] in {"ai", "labs", "inc", "technologies", "security", "health", "industries"}:
        out.append("".join(base[:-1]))
    return list(dict.fromkeys(out))


def detect(name):
    """Return [ats, slug] for the first job board that exists, else None."""
    norm = re.sub(r"[^a-z0-9]", "", name.lower())
    for s in slugs(name):
        b = get(f"https://boards-api.greenhouse.io/v1/boards/{s}")
        # ponytail: name check guards against slug collisions; fuzzy match is a prefix test
        if b and re.sub(r"[^a-z0-9]", "", b.get("name", "").lower()).startswith(norm[:5]):
            return ["greenhouse", s]
        if s in slugs(name)[:2]:  # Lever/Ashby have no board name to check, so only trust exact-name slugs
            if get(f"https://api.lever.co/v0/postings/{s}?mode=json&limit=1"):
                return ["lever", s]
            a = get(f"https://api.ashbyhq.com/posting-api/job-board/{s}")
            if a and a.get("jobs") is not None:
                return ["ashby", s]
    return None


QUERIES = ["software intern", "software engineer new grad", "university graduate software", "early career software",
           "embedded intern", "engineering intern"]


def post(url, body):
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception:
        return None


def board_jobs(company, ats, s):
    """Yield (id, title, location, url)."""
    if ats == "workday":  # s = "tenant.wdN/Site"; search is relevance-ranked, so ask targeted queries
        host, site = s.split("/")
        for q in QUERIES:
            for j in (post(f"https://{host}.myworkdayjobs.com/wday/cxs/{host.split('.')[0]}/{site}/jobs",
                           {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q}) or {}).get("jobPostings", []):
                if not j.get("externalPath"):
                    continue
                yield f"wd:{host}:{j['externalPath']}", j.get("title", ""), j.get("locationsText", ""), \
                    f"https://{host}.myworkdayjobs.com/{site}{j['externalPath']}"
    elif ats == "smartrecruiters":
        for q in ("intern", "graduate"):
            for j in (get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?q={q}&limit=100") or {}).get("content", []):
                loc = j.get("location") or {}
                yield f"sr:{s}:{j['id']}", j["name"], ", ".join(filter(None, [loc.get("city"), loc.get("region")])), \
                    f"https://jobs.smartrecruiters.com/{s}/{j['id']}"
    elif ats == "amazon":
        for q in QUERIES:
            for j in (get(f"https://www.amazon.jobs/en/search.json?base_query={q.replace(' ', '%20')}&result_limit=100&sort=recent") or {}).get("jobs", []):
                yield f"az:{j['id_icims']}", j["title"], j.get("normalized_location", ""), f"https://www.amazon.jobs{j['job_path']}"
    elif ats == "eightfold":  # s = "host|domain"
        host, dom = s.split("|")
        for q in ("intern", "new grad", "university"):
            q = q.replace(' ', '%20')
            r = get(f"https://{host}/api/apply/v2/jobs?domain={dom}&query={q}&num=50") or {}
            # newer Eightfold sites only answer the pcsx API (10 per page, enough for these narrow queries)
            for j in r.get("positions") or ((get(f"https://{host}/api/pcsx/search?domain={dom}&query={q}&start=0") or {})
                                            .get("data") or {}).get("positions", []):
                yield f"ef:{dom}:{j['id']}", j["name"], j.get("location") or ", ".join(j.get("locations", [])), \
                    j.get("canonicalPositionUrl") or f"https://{host}/careers/job/{j['id']}"
    elif ats == "oracle":  # Oracle Recruiting Cloud; s = "host|siteNumber"
        host, site = s.split("|")
        for q in QUERIES:
            r = get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
                    f"&finder=findReqs;siteNumber={site},keyword={q.replace(' ', '%20')},limit=100") or {}
            for j in (r.get("items") or [{}])[0].get("requisitionList", []):
                yield f"or:{host}:{j['Id']}", j["Title"], j.get("PrimaryLocation", ""), \
                    f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}"
    elif ats == "greenhouse":
        for j in (get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs") or {}).get("jobs", []):
            yield f"gh:{s}:{j['id']}", j["title"], (j.get("location") or {}).get("name", ""), j["absolute_url"]
    elif ats == "lever":
        for j in get(f"https://api.lever.co/v0/postings/{s}?mode=json") or []:
            yield f"lv:{s}:{j['id']}", j["text"], (j.get("categories") or {}).get("location", ""), j["hostedUrl"]
    elif ats == "ashby":
        for j in (get(f"https://api.ashbyhq.com/posting-api/job-board/{s}") or {}).get("jobs", []):
            yield f"ab:{s}:{j['id']}", j["title"], j.get("location", ""), j["jobUrl"]


def wanted(title, need_level=True):
    return ROLE.search(title) and not SENIOR.search(title) and (not need_level or LEVEL.search(title))


def collect(boards):
    jobs = {}
    def one(item):
        company, (ats, s) = item
        out = []
        try:  # one odd posting or broken source must never sink the whole run
            for i, t, loc, u in board_jobs(company, ats, s):
                if t and wanted(t):
                    out.append((i, company, t, loc, u))
        except Exception as e:
            print(f"skipped {company} ({ats}): {type(e).__name__}: {e}")
        return out
    with ThreadPoolExecutor(24) as ex:
        for rows in ex.map(one, [(c, b) for c, b in boards.items() if b]):
            for i, *rest in rows:
                jobs[i] = rest
    for j in get(SIMPLIFY) or []:  # every Simplify listing is an internship, so only the role filter applies
        if j.get("active") and j.get("is_visible") and wanted(j["title"], need_level=False):
            jobs[f"sj:{j['id']}"] = [j["company_name"], j["title"], ", ".join(j.get("locations", [])), j["url"]]
    for j in get(SIMPLIFY_NG) or []:  # new grad list: level is implied, so only the role filter applies
        if j.get("active") and j.get("is_visible") and wanted(j["title"], need_level=False):
            jobs[f"sn:{j['id']}"] = [j["company_name"], j["title"], ", ".join(j.get("locations", [])), j["url"]]
    return jobs


def send(new):
    if not ME:
        sys.exit("Set the GMAIL_ADDRESS secret.")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()  # GitHub Actions secret
    if not pw and sys.platform == "darwin":  # local runs
        pw = subprocess.run(["security", "find-generic-password", "-a", ME, "-s", "job-watcher-gmail", "-w"],
                            capture_output=True, text=True).stdout.strip()
    pw_file = HERE / ".gmail-app-password"  # cron can't read the login Keychain, so it uses this chmod-600 file
    if not pw and pw_file.exists():
        pw = pw_file.read_text().strip()
    if not pw:
        sys.exit("No Gmail app password: set GMAIL_APP_PASSWORD, the Keychain entry, or .gmail-app-password.")
    names = list(dict.fromkeys(c for c, *_ in new))
    msg = EmailMessage()
    msg["From"] = msg["To"] = ME
    msg["Subject"] = f"[New Job] {len(new)} new: " + ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
    msg.set_content("\n\n".join(f"{c} - {t}\n{loc}\n{u}" for c, t, loc, u in new))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(ME, pw)
        s.send_message(msg)


def main():
    dry = "--dry-run" in sys.argv
    boards = json.loads(BOARDS.read_text()) if BOARDS.exists() else {}
    names = [l.strip() for l in COMPANIES.read_text().splitlines() if l.strip() and not l.startswith("#")]
    todo = [n for n in names if n not in boards]  # new companies from the startup routine get detected once
    with ThreadPoolExecutor(16) as ex:
        boards.update(zip(todo, ex.map(detect, todo)))
    BOARDS.write_text(json.dumps(boards, indent=1))

    jobs = collect(boards)
    first_run = not SEEN.exists() or "--seed" in sys.argv  # --seed: record current postings without emailing
    seen = set(json.loads(SEEN.read_text())) if not first_run else set()
    if not any(i.startswith("sn:") for i in seen):  # new-grad list's first run: record it instead of emailing all of it
        seen |= {i for i in jobs if i.startswith("sn:")}
    # A company's first appearance would dump all its open roles at once, so those go to the catch-up file instead.
    seeded = set(json.loads(SEEDED.read_text())) if SEEDED.exists() else {c for c, b in boards.items() if b}
    fresh = {i: v for i, v in jobs.items() if i not in seen}
    quiet = {i: v for i, v in fresh.items() if not i.startswith(("sj:", "sn:")) and v[0] not in seeded}
    new, dupes = [], set()
    for i, v in fresh.items():  # one line per company+title even when posted in several locations
        if i not in quiet and (v[0], v[1].strip().lower()) not in dupes:
            dupes.add((v[0], v[1].strip().lower()))
            new.append(v)
    print(f"{len(jobs)} matching postings ({sum(i.startswith('sj:') for i in jobs)} Simplify), "
          f"{len(new)} new, {len(quiet)} from newly added companies")
    if not jobs:
        sys.exit("Fetched nothing: network or source problem.")  # fail the run instead of passing silently
    if dry:
        print("\n".join(f"{c} - {t} | {u}" for c, t, loc, u in new))
        return

    if first_run:  # seed only, so the first email isn't thousands of old postings
        print(f"Seeded {len(jobs)} current postings from {sum(1 for b in boards.values() if b)} boards + Simplify.")
    elif new:
        send(new)  # exits on failure, so nothing below runs and the jobs retry next time
        with LOG.open("a") as f:
            f.writelines(f"- {c} - {t} ({loc}) {u}\n" for c, t, loc, u in new)
    if quiet and not first_run:
        with OPENINGS.open("a") as f:
            f.write(f"\n# Added {time.strftime('%Y-%m-%d')}: open roles at newly watched companies\n")
            f.writelines(f"- [{t.strip()}]({u}) — {c}, {loc}\n" for c, t, loc, u in sorted(quiet.values()))
    SEEN.write_text(json.dumps(sorted(seen | set(jobs))))  # only after a successful send, so failures retry
    SEEDED.write_text(json.dumps(sorted(seeded | {c for c, b in boards.items() if b})))


if __name__ == "__main__":
    main()
