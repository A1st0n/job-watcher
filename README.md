# job-watcher

I was spending too much time refreshing career pages, and the big internship lists were often a day or two behind the company sites. By then a popular posting already has hundreds of applicants. So I wrote this to check the companies I care about every 15 minutes and email me as soon as something new shows up.

It watches about 460 companies across 15 job board platforms (Workday, Greenhouse, Ashby, Lever, Oracle, Eightfold, SmartRecruiters, iCIMS and a few others), plus the SimplifyJobs internship and new grad lists. Since October 12, 2026 it has logged over 2,700 postings.

## How it works

- `companies.txt` is the list of companies. The first time a company is added, `watcher.py` figures out which job board it uses and saves that in `boards.json`.
- Each check pulls every open role from each board and keeps the ones that are early career (intern, co-op, new grad) and in a field I want (software, DevOps, AI/ML, data, IT). Senior roles get dropped.
- Anything not already in `seen.json` gets emailed to me and added to `new_jobs.md`.
- When a new company is added, its existing openings go to `current_openings.md` instead of my inbox, so adding 20 companies doesn't mean 300 emails.
- It runs on GitHub Actions. A scheduled job checks every 15 minutes for about 5.5 hours, then the next scheduled job takes over. State gets committed back to the repo after each check.

`application-tracker.gs` is a separate Google Apps Script. Every hour it searches my Gmail for application confirmations, rejections and interview invites, and logs them to a Google Sheet. That way I know where I've applied and what's still pending without updating anything by hand.

## Problems I ran into

**Silent failures.** The first version turned every network error into an empty list, so a broken feed looked exactly like a company with no openings. I noticed when Microsoft roles never came through. I ran all 525 companies through the fetch code and found 8 feeds failing quietly: rate limits (HTTP 429), an API that returned 403 on every request, and companies that had switched job boards. I fixed each one, added retries with backoff, and made failures print to the run log so the next one shows up the day it happens.

**Workday rate limits.** Adding pagination to Workday searches multiplied the number of requests and set off a wave of 429s (304 failed requests in one run). Random backoff on retries and capping it at 3 pages per search got that down to zero.

**Push conflicts.** A push from my laptop landed in the middle of a run, and the run's own push failed on a `seen.json` merge conflict. Now the run rebases before pushing, and on a conflict it keeps its own copy of `seen.json`, since that copy already has every open job. Markdown logs merge by keeping both sides.

## Running it yourself

1. Fork the repo and put your companies in `companies.txt`.
2. Add two repo secrets: `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` (a Gmail app password, not your normal one).
3. Turn on Actions. To test locally without sending email:

```
python3 watcher.py --dry-run
```

Edit `QUERIES`, `ROLE` and `LEVEL` in `watcher.py` to change what counts as a match.
