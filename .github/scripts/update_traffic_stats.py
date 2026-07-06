import csv
import json
import os
import urllib.request

REPO = os.environ["REPO"]
TOKEN = os.environ["GH_TOKEN"]
CSV_PATH = "stats/traffic-history.csv"
VIEWS_BADGE_PATH = "stats/badge.json"
CLONES_BADGE_PATH = "stats/badge-clones.json"
FIELDS = ["date", "views", "unique_visitors", "clones", "unique_cloners"]


def fetch(endpoint):
    url = f"https://api.github.com/repos/{REPO}/traffic/{endpoint}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    views = fetch("views")["views"]
    clones = fetch("clones")["clones"]
    clones_by_date = {c["timestamp"][:10]: c for c in clones}

    existing = {}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="") as f:
            for row in csv.DictReader(f):
                existing[row["date"]] = row

    for v in views:
        date = v["timestamp"][:10]
        c = clones_by_date.get(date, {"count": 0, "uniques": 0})
        existing[date] = {
            "date": date,
            "views": v["count"],
            "unique_visitors": v["uniques"],
            "clones": c["count"],
            "unique_cloners": c["uniques"],
        }

    os.makedirs("stats", exist_ok=True)
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for date in sorted(existing):
            writer.writerow(existing[date])

    total_unique_visitors = sum(int(row["unique_visitors"]) for row in existing.values())
    with open(VIEWS_BADGE_PATH, "w") as f:
        json.dump({
            "schemaVersion": 1,
            "label": "unique visitors",
            "message": str(total_unique_visitors),
            "color": "79C0FF",
        }, f)

    total_unique_cloners = sum(int(row["unique_cloners"]) for row in existing.values())
    with open(CLONES_BADGE_PATH, "w") as f:
        json.dump({
            "schemaVersion": 1,
            "label": "unique cloners",
            "message": str(total_unique_cloners),
            "color": "79C0FF",
        }, f)


if __name__ == "__main__":
    main()
