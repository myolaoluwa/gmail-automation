from __future__ import annotations

import base64
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

BASE_DIR = Path(__file__).parent
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]
TOKEN_PATH = BASE_DIR / "token.json"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
STATE_PATH = BASE_DIR / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)


def env_list(name: str) -> set[str]:
    return {value.strip().lower() for value in os.getenv(name, "").split(",") if value.strip()}


def authenticate(force_login: bool = False) -> Credentials:
    credentials = None
    if TOKEN_PATH.exists() and not force_login:
        credentials = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError("Download a Desktop OAuth client JSON file as credentials.json first.")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        credentials = flow.run_local_server(
            port=0,
            prompt="select_account",
            access_type="offline",
            login_hint=os.getenv("LOGIN_HINT", ""),
        )
        TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
        LOGGER.info("Google login completed; session saved in %s", TOKEN_PATH)
    return credentials


def get_services(force_login: bool = False):
    credentials = authenticate(force_login=force_login)
    return build("gmail", "v1", credentials=credentials), build("calendar", "v3", credentials=credentials)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"processed": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def header(headers: list[dict], name: str) -> str:
    return next((item["value"] for item in headers if item["name"].lower() == name.lower()), "")


def message_text(payload: dict) -> str:
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = message_text(part)
        if text:
            return text
    return ""


def parse_event(text: str) -> tuple[datetime, str] | None:
    match = re.search(r"Event:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\|\s*(.+)", text, re.IGNORECASE)
    if not match:
        return None
    start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return start, match.group(2).strip()


def matches_rules(sender: str, subject: str) -> bool:
    senders = env_list("ALLOWED_SENDERS")
    keywords = env_list("REPLY_SUBJECT_KEYWORDS")
    sender_address = re.search(r"<([^>]+)>", sender)
    normalized_sender = (sender_address.group(1) if sender_address else sender).strip().lower()
    sender_ok = not senders or normalized_sender in senders
    subject_ok = not keywords or any(keyword in subject.lower() for keyword in keywords)
    return sender_ok and subject_ok


def reply_body(subject: str) -> str:
    return (f"Thanks for your message about {subject!r}. "
            "I have received it and will follow up shortly.")


def send_reply(gmail, message: dict, body: str) -> None:
    metadata = message["payload"]["headers"]
    reply = EmailMessage()
    reply["To"] = header(metadata, "From")
    reply["Subject"] = header(metadata, "Subject")
    reply["In-Reply-To"] = header(metadata, "Message-ID")
    reply.set_content(body)
    encoded = base64.urlsafe_b64encode(reply.as_bytes()).decode("utf-8")
    gmail.users().messages().send(
        userId="me", body={"raw": encoded, "threadId": message["threadId"]}
    ).execute()


def create_event(calendar, start: datetime, title: str) -> None:
    duration = int(os.getenv("DEFAULT_EVENT_DURATION_MINUTES", "30"))
    end = start + timedelta(minutes=duration)
    calendar_id = os.getenv("CALENDAR_NAME", "primary")
    calendar.events().insert(
        calendarId=calendar_id,
        body={
            "summary": title,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        },
    ).execute()


def process_once(gmail, calendar, state: dict) -> None:
    result = gmail.users().messages().list(userId="me", q="is:unread").execute()
    for item in result.get("messages", []):
        message_id = item["id"]
        if message_id in state["processed"]:
            continue
        message = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
        metadata = message["payload"]["headers"]
        sender = header(metadata, "From")
        subject = header(metadata, "Subject")
        text = message_text(message["payload"])
        if not matches_rules(sender, subject):
            continue

        event = parse_event(text)
        dry_run = os.getenv("DRY_RUN", "true").lower() != "false"
        LOGGER.info("%s message from %s: %s", "Would process" if dry_run else "Processing", sender, subject)
        if event:
            start, title = event
            LOGGER.info("%s calendar event %s at %s", "Would create" if dry_run else "Creating", title, start)
            if not dry_run:
                create_event(calendar, start, title)
        if not dry_run:
            send_reply(gmail, message, reply_body(subject))
            gmail.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            state["processed"].append(message_id)
            save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail and Google Calendar assistant")
    parser.add_argument("--login", action="store_true", help="Open Google sign-in and choose an account")
    parser.add_argument("--logout", action="store_true", help="Remove the locally saved Google session")
    args = parser.parse_args()

    if args.logout:
        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()
            LOGGER.info("Local Google session removed")
        return

    if args.login:
        LOGGER.info("Google login flow started; continuing with the worker after authorization completes.")
        authenticate(force_login=True)

    gmail, calendar = get_services()
    state = load_state()
    poll_seconds = int(os.getenv("POLL_SECONDS", "300"))
    while True:
        try:
            process_once(gmail, calendar, state)
        except Exception:
            LOGGER.exception("Processing cycle failed")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
