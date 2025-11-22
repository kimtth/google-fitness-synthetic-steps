import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, date, timedelta
from typing import Dict, List, Tuple, Optional

from dateutil import tz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scopes required to read/write activity data
SCOPES = ["https://www.googleapis.com/auth/fitness.activity.write", "https://www.googleapis.com/auth/fitness.activity.read"]

# We removed the hardcoded DATA_SOURCE_ID constant to prevent project number errors.
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
    parser.add_argument("--timezone", default=time.tzname[0], help="Timezone name (e.g. UTC, America/Los_Angeles)")
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


def ensure_data_source(service) -> str:
    """
    Checks if the data source exists. If not, creates it without specifying the ID,
    allowing Google to generate the correct ID with the Project Number.
    Returns the valid dataSourceId.
    """
    try:
        # 1. List existing sources to see if we already created one
        existing_sources = service.users().dataSources().list(userId="me").execute()
        for ds in existing_sources.get("dataSource", []):
            # We match based on Stream Name and App Name
            if (ds.get("dataStreamName") == DATA_STREAM_NAME and 
                ds.get("application", {}).get("name") == APPLICATION_NAME):
                print(f"Found existing data source: {ds['dataStreamId']}")
                return ds['dataStreamId']

        # 2. Create new source if not found
        print("Creating new data source...")
        body = {
            "dataStreamName": DATA_STREAM_NAME,
            "type": "raw",
            "application": {"name": APPLICATION_NAME},
            "dataType": {
                "name": "com.google.step_count.delta",
                "field": [{"name": "steps", "format": "integer"}]
            }
            # NOTE: We do NOT include "dataStreamId" here. 
            # Google will generate it using the correct Project Number.
        }
        response = service.users().dataSources().create(userId="me", body=body).execute()
        new_id = response['dataStreamId']
        print(f"Created new data source: {new_id}")
        return new_id

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
        # Start of day in specific timezone
        start_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tzinfo)
        # End of day (start + 24h minus 1 nanosecond technically, but straight logic handles ranges)
        end_dt = start_dt + timedelta(days=1)
        
        # Google Fit requires strict nanosecond ranges
        points.append({
            "startTimeNanos": str(to_ns(start_dt)),
            "endTimeNanos": str(to_ns(end_dt)),
            "dataTypeName": "com.google.step_count.delta",
            "value": [{"intVal": steps}]
        })
    return points


def upload_steps(service, points: List[Dict], tzinfo, verbose: bool, data_source_id: str):
    if not points:
        print("No points to upload")
        return
        
    start_ns = points[0]["startTimeNanos"]
    end_ns = points[-1]["endTimeNanos"]
    
    # The dataset ID is a range: min-max
    dataset_id = f"{start_ns}-{end_ns}"
    
    body = {
        "dataSourceId": data_source_id,
        "minStartTimeNs": start_ns,
        "maxEndTimeNs": end_ns,
        "point": points
    }
    
    try:
        if verbose:
            print(f"Uploading to {data_source_id}...")
            print(json.dumps(body, indent=2)[:500] + "... (truncated)")
            
        service.users().dataSources().datasets().patch(
            userId="me",
            dataSourceId=data_source_id,
            datasetId=dataset_id,
            body=body
        ).execute()
        print(f"Successfully uploaded {len(points)} day(s) of step data.")
        
    except HttpError as e:
        print(f"Upload failed: {e}")
        # Often useful to see full error detail
        try:
            error_details = json.loads(e.content.decode('utf-8'))
            print(json.dumps(error_details, indent=2))
        except:
            pass
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
    
    # Dynamically get the correct Data Source ID
    data_source_id = ensure_data_source(service)
    
    points = build_points(steps_map, tzinfo)
    upload_steps(service, points, tzinfo, args.verbose, data_source_id)


if __name__ == "__main__":
    main()