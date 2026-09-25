# Gmail and Calendar assistant

A local, OAuth-authenticated Gmail/Google Calendar worker. It starts in dry-run mode and only processes messages that match the rules in `.env`.

## Setup

1. Create a Google Cloud project and enable the Gmail API and Google Calendar API.
2. Configure the OAuth consent screen for an external/personal account. Add your own Google account as a test user if the app is still in testing.
3. Create an OAuth client ID for a **Desktop app**, download the JSON file, and save it as `credentials.json` in this folder.
4. Create a virtual environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

5. Copy `.env.example` to `.env` and configure the rules:

```text
DRY_RUN=true
ALLOWED_SENDERS=person@example.com,another@example.com
REPLY_SUBJECT_KEYWORDS=meeting,question
CALENDAR_NAME=primary
DEFAULT_EVENT_DURATION_MINUTES=30
```

6. Run `py main.py --login`. A browser opens directly to Google sign-in, where you can choose your account and complete consent. The script stores only the OAuth refresh token locally in `token.json`; it never asks for or stores your Google password. You can prefill the account chooser with `LOGIN_HINT=you@gmail.com`.

After signing in, run `py main.py` to start the worker. Use `py main.py --login` any time you want to switch accounts or grant access again, and `py main.py --logout` to remove the local session.

Set `DRY_RUN=false` only after reviewing the logged actions. The worker replies with a short acknowledgment and creates calendar events only when a matching message contains a line in the form `Event: YYYY-MM-DD HH:MM | Title`.

## Behavior

- Reads unread messages from Gmail.
- Ignores messages outside `ALLOWED_SENDERS` or without a configured subject keyword.
- In dry-run mode, logs intended replies and events without changing Google data.
- In live mode, replies in the existing thread, creates an event in the configured calendar, and labels processed messages.
- Stores processed message IDs in `state.json` to avoid duplicate actions.

This is intentionally a narrow starting point. Expand the rules and reply templates only after testing with dry-run logs.
