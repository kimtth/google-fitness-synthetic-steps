import argparse
import json
import os
import random
import sys
from datetime import datetime, date, timedelta
from typing import Dict, List, Tuple

from dateutil import tz

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/fitness.activity.write"]
DATA_SOURCE_ID = "raw:com.google.step_count.delta:GitHubCopilot:synthetic_steps"
DATA_STREAM_NAME = "Synthetic Steps"
APPLICATION_NAME = "SyntheticStepUploader"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload synthetic daily step counts to Google Fit.")
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--date", help="Single date YYYY-MM-DD")
    date_group.add_argument("--start-date", help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD (inclusive) required if --start-date used")
    parser.add_argument("--min", type=int, default=5000, help="Global minimum steps per day")
    parser.add_argument("--max", type=int, default=12000, help="Global maximum steps per day")
    parser.add_argument("--ranges", help="Comma list of per-date overrides: YYYY-MM-DD:min-max")
    parser.add_argument("--timezone", default=str(tz.tzlocal()), help="Timezone name (e.g. UTC, America/Los_Angeles)")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload, just display planned steps")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser.parse_args()


def load_credentials() -> Credentials:
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("client_secrets.json"):
                print("Missing client_secrets.json; create OAuth client and place file here.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def ensure_data_source(service) -> None:
    try:
        existing_sources = service.users().dataSources().list(userId="me").execute()
        for ds in existing_sources.get("dataSource", []):
            if ds.get("dataStreamId") == DATA_SOURCE_ID:
                return  # exists
        body = {
            "dataStreamName": DATA_STREAM_NAME,
            "type": "raw",
            "application": {"name": APPLICATION_NAME},
            "dataType": {
                "name": "com.google.step_count.delta",
                "field": [{"name": "steps", "format": "integer"}]
            },
            "dataStreamId": DATA_SOURCE_ID
        }
        service.users().dataSources().create(userId="me", body=body).execute()
    except HttpError as e:
        print(f"Failed ensuring data source: {e}")
        sys.exit(1)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def build_date_list(args: argparse.Namespace) -> List[date]:
    if args.date:
        return [parse_date(args.date)]
    start = parse_date(args.start_date)  # type: ignore
    if not args.end_date:
        print("--end-date required when using --start-date")
        sys.exit(1)
    end = parse_date(args.end_date)
    if end < start:
        print("End date before start date")
        sys.exit(1)
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def parse_ranges(ranges_str: str) -> Dict[date, Tuple[int, int]]:
    mapping = {}
    if not ranges_str:
        return mapping
    for part in ranges_str.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            d_str, mm = part.split(':', 1)
            min_s, max_s = mm.split('-', 1)
            d = parse_date(d_str)
            mapping[d] = (int(min_s), int(max_s))
        except ValueError:
            print(f"Invalid range component: {part}")
            sys.exit(1)
    return mapping


def generate_steps_for_dates(dates: List[date], global_min: int, global_max: int, overrides: Dict[date, Tuple[int, int]]) -> Dict[date, int]:
    result = {}
    for d in dates:
        mn, mx = overrides.get(d, (global_min, global_max))
        if mn > mx:
            print(f"Min greater than max for {d}")
            sys.exit(1)
        result[d] = random.randint(mn, mx)
    return result


def to_ns(dt_obj: datetime) -> int:
    return int(dt_obj.timestamp() * 1e9)


def build_points(step_map: Dict[date, int], tzinfo) -> List[Dict]:
    points = []
    for d, steps in step_map.items():
        start_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tzinfo)
        end_dt = start_dt + timedelta(days=1)
        points.append({
            "startTimeNanos": str(to_ns(start_dt)),
            "endTimeNanos": str(to_ns(end_dt)),
            "dataTypeName": "com.google.step_count.delta",
            "value": [{"intVal": steps}]
        })
    return points


def upload_steps(service, points: List[Dict], tzinfo, verbose: bool):
    if not points:
        print("No points to upload")
        return
    start_ns = points[0]["startTimeNanos"]
    end_ns = points[-1]["endTimeNanos"]
    dataset_id = f"{start_ns}-{end_ns}"
    body = {
        "dataSourceId": DATA_SOURCE_ID,
        "minStartTimeNs": start_ns,
        "maxEndTimeNs": end_ns,
        "point": points
    }
    try:
        if verbose:
            print(json.dumps(body, indent=2)[:1000])
        service.users().dataSources().datasets().patch(
            userId="me",
            dataSourceId=DATA_SOURCE_ID,
            datasetId=dataset_id,
            body=body
        ).execute()
        print(f"Uploaded {len(points)} day(s) of step data.")
    except HttpError as e:
        print(f"Upload failed: {e}")
        sys.exit(1)


def main():
    args = parse_args()
    tzinfo = tz.gettz(args.timezone)
    if tzinfo is None:
        print(f"Unknown timezone: {args.timezone}")
        sys.exit(1)

    dates = build_date_list(args)
    overrides = parse_ranges(args.ranges) if args.ranges else {}
    steps_map = generate_steps_for_dates(dates, args.min, args.max, overrides)

    print("Planned steps:")
    for d in dates:
        print(f"  {d}: {steps_map[d]} steps")

    if args.dry_run:
        print("Dry-run: no data uploaded.")
        return

    creds = load_credentials()
    service = build("fitness", "v1", credentials=creds, cache_discovery=False)
    ensure_data_source(service)
    points = build_points(steps_map, tzinfo)
    upload_steps(service, points, tzinfo, args.verbose)


if __name__ == "__main__":
    main()

# Example usage:
# python -m venv .venv
# .venv\Scripts\activate
# pip install -r requirements.txt
# python fit_steps.py --date 2025-11-20 --min 8000 --max 11000 --dry-run
