import argparse
import json
import sys
import time

from .state import State
from .ucla import SourceError, check, find_sections


def main(argv=None):
    parser = argparse.ArgumentParser(description="UCLA public enrollment checker (hourly source; not real time).")
    parser.add_argument("--url", help="A UCLA public ClassDetail link for an exact section")
    parser.add_argument("--term", help="UCLA term code, for example 27W")
    parser.add_argument("--subject", help='UCLA subject abbreviation, for example "COM SCI"')
    parser.add_argument("--course", help="Numeric course number, for example 31")
    parser.add_argument("--class-id", help="Choose an exact class ID when looking up a course")
    parser.add_argument("--list", action="store_true", help="List lecture sections and their public detail links")
    parser.add_argument("--watch", action="store_true", help="Keep checking while this process runs")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between checks (minimum 3600)")
    parser.add_argument("--state", default=".local/state.sqlite3", help="Local SQLite state path")
    parser.add_argument("--json", action="store_true", help="Print one JSON object per observation")
    args = parser.parse_args(argv)
    if args.interval < 3600:
        parser.error("The public detail source refreshes hourly; interval must be at least 3600 seconds.")
    if args.url and any([args.term, args.subject, args.course, args.class_id, args.list]):
        parser.error("Use --url alone to identify a section, or use --term, --subject, --course and optionally --class-id.")
    if not args.url and not all([args.term, args.subject, args.course]):
        parser.error("Supply --url, or all of --term, --subject and --course.")
    state = None
    try:
        if args.url:
            url = args.url
        else:
            sections = find_sections(args.term.upper(), args.subject, args.course)
            if args.list:
                if args.json:
                    print(json.dumps(sections))
                else:
                    for item in sections:
                        print(f"{item['class_id']}  {item['section']}\n{item['url']}")
                return 0
            if args.class_id:
                sections = [s for s in sections if s["class_id"] == args.class_id]
            if len(sections) != 1:
                raise SourceError("Choose an exact section with --class-id. Run with --list to see the available IDs.")
            url = sections[0]["url"]
        state = State(args.state)
        failures = 0
        while True:
            try:
                snapshot = check(url)
                reopened = state.record(snapshot)
                failures = 0
                if args.json:
                    print(json.dumps({**snapshot.as_dict(), "reopened": reopened}), flush=True)
                else:
                    print(f"{snapshot.course} | {snapshot.section} | ID {snapshot.class_id}")
                    print(f"Status: {snapshot.status}; seats left: {snapshot.available}; capacity: {snapshot.capacity}")
                    print(f"Waitlist: {snapshot.waitlist}\nRestrictions: {snapshot.restrictions}")
                    print(f"Fetched at: {snapshot.checked_at}\nSource notice: {snapshot.source_notice}")
                    print("Public cached source; confirm eligibility and current availability in MyUCLA.")
                    if reopened:
                        print(f"REOPENED since the previous successful check: {snapshot.enrollment_url}")
                    print(flush=True)
            except SourceError as exc:
                print(f"Availability unknown: {exc} Last successful observation preserved.", file=sys.stderr, flush=True)
                if not args.watch:
                    return 1
                failures += 1
            if not args.watch:
                return 0
            time.sleep(args.interval * min(2 ** min(failures, 2), 4))
    except (SourceError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if state:
            state.close()
