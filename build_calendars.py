"""Builds personal .ics calendar feeds: Formula 1, Ajax and the Dutch national team.

Data sources (free, no API key):
  - Jolpica F1 API (successor of Ergast) for F1 sessions
  - ESPN's public site API for football fixtures and results, all competitions

Output goes to docs/, which GitHub Pages serves so calendar apps can subscribe.
Standard library only, so it runs anywhere without installing packages.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent / "docs"
UID_DOMAIN = "my-calendars.goofvissers.github.io"
REFRESH = "PT6H"  # hint to calendar apps how often to re-fetch

F1_SESSIONS = [  # (API key, label, duration in minutes)
    ("FirstPractice", "Practice 1", 60),
    ("SecondPractice", "Practice 2", 60),
    ("ThirdPractice", "Practice 3", 60),
    ("SprintQualifying", "Sprint Qualifying", 45),
    ("Sprint", "Sprint", 60),
    ("Qualifying", "Qualifying", 60),
]
RACE_MINUTES = 120
MATCH_MINUTES = 120

TEAMS = [  # (file slug, calendar name, ESPN team id)
    ("ajax", "Ajax", 139),
    ("oranje", "Nederlands elftal", 449),
]


def get_json(url):
    # Keep Python's default User-Agent: ESPN answers 403 to custom and browser-like ones.
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def parse_utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


# --- Formula 1 -------------------------------------------------------------

def f1_events():
    year = datetime.now(timezone.utc).year
    events = []
    for season in (year, year + 1):  # next season appears once it's published
        data = get_json(f"https://api.jolpi.ca/ergast/f1/{season}/races/?limit=100")
        for race in data["MRData"]["RaceTable"]["Races"]:
            gp = race["raceName"].replace("Grand Prix", "GP")
            circuit = race["Circuit"]
            place = circuit["Location"]
            location = f"{circuit['circuitName']}, {place['locality']}, {place['country']}"
            sessions = [(key, label, mins, race[key]) for key, label, mins in F1_SESSIONS if key in race]
            sessions.append(("Race", "Race", RACE_MINUTES, {"date": race["date"], "time": race.get("time")}))
            for key, label, minutes, when in sessions:
                time = when.get("time")
                events.append({
                    "uid": f"f1-{season}-{race['round']}-{key.lower()}",
                    "summary": f"F1: {gp} – {label}",
                    "start": parse_utc(f"{when['date']}T{time or '00:00:00Z'}"),
                    "all_day": not time,
                    "minutes": minutes,
                    "location": location,
                    "description": f"Round {race['round']} of the {season} season\n{race['url']}",
                })
    return events


# --- Football ----------------------------------------------------------------

def team_name(side):
    team = side["team"]
    return (team.get("shortDisplayName") or team["displayName"]).strip()


def score(side):
    value = side.get("score")
    if isinstance(value, dict):
        value = value.get("displayValue")
    return value if value not in (None, "") else "?"


def team_events(team_id):
    base = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule"
    matches = {}
    for url in (base, base + "?fixture=true"):  # played results + upcoming fixtures
        for event in get_json(url).get("events", []):
            matches[event["id"]] = event

    events = []
    for event in matches.values():
        comp = event["competitions"][0]
        sides = {side["homeAway"]: side for side in comp["competitors"]}
        home, away = team_name(sides["home"]), team_name(sides["away"])
        status = comp["status"]["type"]

        if status.get("completed"):
            summary = f"{home} {score(sides['home'])}–{score(sides['away'])} {away}"
            shootout = (sides["home"].get("shootoutScore"), sides["away"].get("shootoutScore"))
            if None not in shootout:
                summary += f" (pen. {shootout[0]:g}–{shootout[1]:g})"
            elif status.get("name") == "STATUS_FINAL_PEN":
                winner = next((team_name(s) for s in sides.values() if s.get("winner")), None)
                summary += f" ({winner} wins on pens)" if winner else " (pen.)"
        else:
            summary = f"{home} – {away}"
            if status.get("state") == "post":  # ended without a result: postponed, cancelled, ...
                summary = f"{status.get('description', 'Postponed')}: {summary}"

        timed = comp.get("timeValid", event.get("timeValid", True))
        if not timed and not status.get("completed"):
            summary += " (time TBC)"

        venue = comp.get("venue") or {}
        city = (venue.get("address") or {}).get("city")
        location = ", ".join(part for part in (venue.get("fullName"), city) if part)

        league = event.get("league", {}).get("name", "")
        stage = event.get("seasonType", {}).get("name", "")
        details = [league, stage if league not in stage else ""]  # skip "2026 Club Friendly" style repeats
        channels = [name for b in comp.get("broadcasts", []) for name in b.get("names", [])]
        if channels:
            details.append("TV: " + ", ".join(channels))

        events.append({
            "uid": f"espn-{event['id']}",
            "summary": summary,
            "start": parse_utc(event["date"]),
            "all_day": not timed,
            "minutes": MATCH_MINUTES,
            "location": location,
            "description": "\n".join(d for d in details if d),
        })
    return events


# --- iCalendar output ---------------------------------------------------------

def ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """Split lines longer than 75 bytes, as the iCalendar spec requires."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts, current = [], ""
    for ch in line:
        limit = 75 if not parts else 74  # continuation lines start with a space
        if len((current + ch).encode("utf-8")) > limit:
            parts.append(current)
            current = ""
        current += ch
    parts.append(current)
    return "\r\n ".join(parts)


def fmt_utc(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def render_calendar(name, events):
    stamp = fmt_utc(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//goofvissers//my-calendars//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(name)}",
        "X-WR-TIMEZONE:Europe/Amsterdam",
        f"X-PUBLISHED-TTL:{REFRESH}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}",
    ]
    for ev in sorted(events, key=lambda e: e["start"]):
        lines += ["BEGIN:VEVENT", f"UID:{ev['uid']}@{UID_DOMAIN}", f"DTSTAMP:{stamp}"]
        if ev["all_day"]:
            day = ev["start"].date()
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        else:
            end = ev["start"] + timedelta(minutes=ev["minutes"])
            lines += [f"DTSTART:{fmt_utc(ev['start'])}", f"DTEND:{fmt_utc(end)}"]
        lines.append(f"SUMMARY:{ics_escape(ev['summary'])}")
        if ev.get("location"):
            lines.append(f"LOCATION:{ics_escape(ev['location'])}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{ics_escape(ev['description'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def without_stamps(text):
    return [line for line in text.splitlines() if not line.startswith("DTSTAMP:")]


def write_calendar(slug, name, events):
    """Write docs/<slug>.ics, skipping the write when only timestamps would change."""
    path = OUT / f"{slug}.ics"
    content = render_calendar(name, events)
    if path.exists() and without_stamps(path.read_text(encoding="utf-8")) == without_stamps(content):
        print(f"{slug}: {len(events)} events, unchanged")
        return
    OUT.mkdir(exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    print(f"{slug}: {len(events)} events, written")


def main():
    jobs = [("f1", "Formula 1", f1_events)]
    jobs += [(slug, name, lambda team_id=team_id: team_events(team_id)) for slug, name, team_id in TEAMS]
    failed = False
    for slug, name, fetch in jobs:
        try:
            events = fetch()
            if not events:
                raise RuntimeError("source returned no events")
            write_calendar(slug, name, events)
        except Exception as exc:  # keep the previous file so subscribers never get an empty calendar
            print(f"{slug}: FAILED ({exc}), keeping previous file", file=sys.stderr)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
