# Delivery Stack Research: Google Sheets + Morning Email

> Research agent R4 -- April 2026
> Scope: daily lead summary delivery for NadiaAI (1-50 rows/day, single user, GitHub Actions cron)

---

## Recommended Stack

| Component | Choice | Reason |
|-----------|--------|--------|
| Sheets library | **gspread 6.x** + **gspread-formatting** | Mature, well-documented, dict-based auth fits CI perfectly |
| Email transport | **Gmail SMTP (port 465, SSL)** via `smtplib` | Zero cost, zero vendor signup, proven in GitHub Actions |
| Secrets management | Base64-encoded service-account JSON in GitHub Actions secrets | Standard pattern; gspread's `service_account_from_dict()` consumes it directly |

---

## 1. Google Sheets via gspread

### 1.1 Installation

```bash
pip install gspread gspread-formatting
```

### 1.2 Service Account Setup (One-Time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Navigate to **APIs & Services > Library**.
4. Search for and enable **Google Drive API**.
5. Search for and enable **Google Sheets API**.
6. Go to **APIs & Services > Credentials**.
7. Click **Create credentials > Service account**.
8. Fill in a name (e.g., `nadia-sheets-bot`) and click **Create and Continue**.
9. Skip the optional role/access steps; click **Done**.
10. In the service accounts list, click the new account's email.
11. Go to **Keys > Add Key > Create new key > JSON > Create**.
12. A JSON file downloads automatically. Keep it safe.
13. Open the target Google Sheet in your browser.
14. Click **Share** and paste the `client_email` value from the JSON file (e.g., `nadia-sheets-bot@your-project.iam.gserviceaccount.com`).
15. Grant **Editor** access. Click **Send**.

### 1.3 Authentication (In Code)

For GitHub Actions, the JSON credential is stored as a base64-encoded secret
and decoded at runtime:

```python
import base64
import json
import os
import gspread

# Decode the service account JSON from the environment variable
sa_json = json.loads(
    base64.b64decode(os.environ["GOOGLE_SA_JSON_B64"]).decode("utf-8")
)
gc = gspread.service_account_from_dict(sa_json)
```

For local development you can point to the file directly:

```python
gc = gspread.service_account(filename="service_account.json")
```

### 1.4 Opening a Sheet and Reading Rows

```python
SHEET_ID = "1aBcDeFgHiJkLmNoPqRsTuVwXyZ..."  # from the Sheet URL

spreadsheet = gc.open_by_key(SHEET_ID)
worksheet = spreadsheet.sheet1  # or spreadsheet.worksheet("Leads")

# Read all rows as list of dicts (header row = keys)
existing = worksheet.get_all_records()  # -> List[Dict[str, Any]]

# Or as raw list of lists
all_values = worksheet.get_all_values()  # -> List[List[str]]
```

### 1.5 Deduplication Before Appending

```python
# Build a set of existing unique keys (e.g., phone number in column 3)
existing_keys = {row["Telefono"] for row in existing}

# Filter new leads
new_leads = [
    lead for lead in todays_leads
    if lead["phone"] not in existing_keys
]
```

### 1.6 Appending Rows

```python
from gspread.utils import ValueInputOption

if new_leads:
    rows = [
        [
            lead["date"],
            lead["name"],
            lead["phone"],
            lead["source"],
            lead["score"],
            lead["notes"],
        ]
        for lead in new_leads
    ]
    worksheet.append_rows(
        rows,
        value_input_option=ValueInputOption.user_entered,  # parses dates/numbers
        table_range="A1",  # append below the detected table starting at A1
    )
```

`append_rows` makes a single API call for all rows, so 50 rows = 1 write request.

### 1.7 Freeze Header Row

```python
worksheet.freeze(rows=1)  # freeze first row
```

### 1.8 Auto-Resize Columns

```python
# Auto-resize columns A through F (indices 0..6, end exclusive)
worksheet.columns_auto_resize(0, 6)
```

### 1.9 Conditional Formatting (Highlight Last-24h Rows)

Requires `gspread-formatting`. Uses a `CUSTOM_FORMULA` BooleanCondition that
compares a date column (assumed column A) against `TODAY()`:

```python
from gspread_formatting import (
    ConditionalFormatRule,
    BooleanRule,
    BooleanCondition,
    CellFormat,
    Color,
    GridRange,
    get_conditional_format_rules,
)

rule = ConditionalFormatRule(
    ranges=[GridRange.from_a1_range("A2:F5000", worksheet)],
    booleanRule=BooleanRule(
        condition=BooleanCondition(
            "CUSTOM_FORMULA",
            ["=AND($A2<>\"\", $A2>=TODAY())"]
        ),
        format=CellFormat(
            backgroundColor=Color(1, 1, 0.8)  # light yellow
        ),
    ),
)

rules = get_conditional_format_rules(worksheet)
rules.append(rule)
rules.save()
```

> **Note**: The conditional format rule is persistent -- it only needs to be set
> once (during initial sheet setup), not on every run. The formula
> `$A2>=TODAY()` re-evaluates automatically when the sheet is opened.

### 1.10 One-Time Sheet Setup Helper

Combine freeze, auto-resize, and conditional formatting in a setup function
that runs once (or is idempotent):

```python
def setup_sheet(worksheet):
    """Run once to configure the sheet layout."""
    worksheet.freeze(rows=1)
    worksheet.columns_auto_resize(0, 6)

    # Clear existing conditional format rules and set ours
    rules = get_conditional_format_rules(worksheet)
    rules.clear()
    rules.append(rule)  # the rule from 1.9
    rules.save()
```

---

## 2. Quota Analysis

### Google Sheets API Quotas (Free Tier)

| Metric | Limit |
|--------|-------|
| Read requests per minute per project | 300 |
| Read requests per minute per user | 60 |
| Write requests per minute per project | 300 |
| Write requests per minute per user | 60 |
| Daily limit | **None** (unlimited as long as per-minute quotas are respected) |
| Per-request payload | Recommended max 2 MB |
| Per-request timeout | 180 seconds |

### Our Usage Per Run

| Operation | API calls | Type |
|-----------|-----------|------|
| `get_all_records()` (dedup read) | 1 | Read |
| `append_rows()` (1-50 rows, single batch) | 1 | Write |
| `freeze()` | 1 | Write |
| `columns_auto_resize()` | 1 | Write |
| Conditional formatting (save) | 1 | Write |
| **Total** | **5** | 1 read + 4 writes |

**Verdict**: We use 5 requests per run, once per day. The per-minute limit is
300. We are at **1.7% of the per-minute quota** and there is no daily cap.
We will never hit quota limits -- not even close. Even if we ran the pipeline
hourly (24x/day = 120 requests/day), we would still be fine.

**Cost**: The Google Sheets API is free. There is no paid tier for basic
read/write operations. The service account itself is free.

---

## 3. Email Delivery

### 3.1 Recommended: Gmail SMTP with App Password

**Why Gmail SMTP over a transactional service?**

- Zero cost, forever. No free-tier expiry.
- No vendor account to create or domain to verify.
- The end user already has a Gmail account.
- 1 email/day is trivially below any Gmail sending limit (500/day for personal, 2000/day for Workspace).
- Simpler dependency chain (stdlib only, no third-party SDK).

### 3.2 Gmail App Password Setup (One-Time)

1. Sign in to the Gmail account that will send the emails.
2. Go to [Google Account Security](https://myaccount.google.com/security).
3. Enable **2-Step Verification** if not already enabled.
4. Go to [App Passwords](https://myaccount.google.com/apppasswords).
5. Select **Mail** as the app.
6. Select **Other (Custom name)** and type `NadiaAI`.
7. Click **Generate**.
8. Copy the 16-character password shown on screen.
9. Store it as a GitHub Actions secret named `GMAIL_APP_PASSWORD`.

### 3.3 SMTP Settings

| Parameter | Value |
|-----------|-------|
| Host | `smtp.gmail.com` |
| Port | `465` (implicit TLS/SSL) |
| Security | SSL (use `smtplib.SMTP_SSL`) |
| Username | Full Gmail address (e.g., `nadia@gmail.com`) |
| Password | The 16-character app password |

> **Why port 465 over 587?** Port 465 uses implicit TLS (wrapped from the
> start) and is confirmed working from GitHub Actions runners. Port 587 uses
> STARTTLS and some reports indicate intermittent timeouts from Azure-hosted
> runners. Port 465 is the safer choice.

### 3.4 Python Email Sending Code

```python
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os

def send_lead_summary(
    to_email: str,
    subject: str,
    html_body: str,
    plain_body: str,
):
    """Send an HTML email with plain-text fallback via Gmail SMTP."""
    from_email = os.environ["GMAIL_USER"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    # Plain text first, HTML second (RFC 2046: last part is preferred)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_email, app_password)
        server.sendmail(from_email, to_email, msg.as_string())
```

### 3.5 Example Email Content (Spanish)

```python
from datetime import date

def build_email(new_leads: list, sheet_url: str) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_body) for the daily summary."""
    today = date.today().strftime("%d/%m/%Y")
    count = len(new_leads)
    subject = f"NadiaAI: {count} leads nuevos - {today}"

    # Top 5 leads for the summary
    top = new_leads[:5]

    # Plain text version
    lines = [f"Buenos dias,\n\nHoy se encontraron {count} leads nuevos.\n"]
    for i, lead in enumerate(top, 1):
        lines.append(f"{i}. {lead['name']} - {lead['phone']} ({lead['source']})")
    if count > 5:
        lines.append(f"\n...y {count - 5} mas.")
    lines.append(f"\nVer todos en la hoja: {sheet_url}")
    plain_body = "\n".join(lines)

    # HTML version
    rows_html = ""
    for lead in top:
        rows_html += (
            f"<tr><td>{lead['name']}</td>"
            f"<td>{lead['phone']}</td>"
            f"<td>{lead['source']}</td>"
            f"<td>{lead['score']}</td></tr>"
        )
    html_body = f"""
    <html><body>
    <p>Buenos dias,</p>
    <p>Hoy se encontraron <strong>{count}</strong> leads nuevos:</p>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse; font-family:sans-serif;">
      <tr style="background:#f0f0f0;">
        <th>Nombre</th><th>Telefono</th><th>Fuente</th><th>Score</th>
      </tr>
      {rows_html}
    </table>
    {"<p><em>...y " + str(count - 5) + " mas.</em></p>" if count > 5 else ""}
    <p><a href="{sheet_url}">Abrir hoja de Google Sheets</a></p>
    </body></html>
    """
    return subject, html_body, plain_body
```

---

## 4. GitHub Actions Integration

### 4.1 Secrets Required

| Secret name | Value | How to generate |
|-------------|-------|-----------------|
| `GOOGLE_SA_JSON_B64` | Base64-encoded service account JSON | `base64 -w 0 service_account.json` (Linux) or `certutil -encode` (Windows) |
| `GMAIL_USER` | Full Gmail address | The sender's email |
| `GMAIL_APP_PASSWORD` | 16-char app password | Google Account > Security > App Passwords |

### 4.2 Encoding the Service Account JSON

```bash
# Linux / macOS / Git Bash on Windows
base64 -w 0 < service_account.json
# Copy the output and paste it as the GitHub secret value
```

In Python at runtime:

```python
import base64, json, os

sa_info = json.loads(
    base64.b64decode(os.environ["GOOGLE_SA_JSON_B64"]).decode("utf-8")
)
```

### 4.3 Workflow YAML Snippet

```yaml
name: Daily Lead Summary
on:
  schedule:
    - cron: "0 12 * * *"   # 12:00 UTC = ~7:00 AM Mexico City
  workflow_dispatch:         # allow manual trigger

jobs:
  deliver:
    runs-on: ubuntu-latest
    env:
      GOOGLE_SA_JSON_B64: ${{ secrets.GOOGLE_SA_JSON_B64 }}
      GMAIL_USER: ${{ secrets.GMAIL_USER }}
      GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install gspread gspread-formatting

      - name: Run delivery pipeline
        run: python src/delivery.py
```

### 4.4 GitHub Actions SMTP Gotchas

| Concern | Status |
|---------|--------|
| Port 465 (SMTP SSL) outbound | **Allowed**. GitHub-hosted runners on Azure do not block outbound TCP 465. Multiple production workflows confirm this works. |
| Port 587 (STARTTLS) outbound | **Usually works** but intermittent timeout reports exist. Port 465 is safer. |
| Port 25 (plain SMTP) | **Blocked** on most cloud providers. Do not use. |
| IP reputation | GitHub runner IPs rotate and are shared. Gmail's own SMTP server authenticates via app password so IP reputation is not a factor (you are sending through Google's infrastructure, not directly). |
| Secret masking | GitHub Actions automatically masks secret values in logs. The base64 blob will not leak in build output. |
| Runner timeout | Default job timeout is 6 hours. Our job takes ~10 seconds. No concern. |

---

## 5. Rejected Alternatives

| Alternative | Reason for rejection |
|-------------|---------------------|
| **Google Apps Script (webhook)** | Adds a second runtime/language to maintain; gspread is simpler and keeps all logic in Python within the existing GitHub Actions pipeline. |
| **Resend (free tier: 3,000/month, 100/day)** | Unnecessary vendor dependency for 1 email/day; requires domain verification and API key management; Gmail SMTP is simpler. |
| **Mailgun (free tier: 100/day)** | Same reasoning as Resend, plus Mailgun's free tier is a sandbox restricted to verified recipients only -- unusable for production without a paid plan. |
| **SendGrid (free tier: 100/day)** | Requires domain authentication and sender verification; overkill for a single daily email. |
| **Amazon SES** | Requires AWS account, IAM setup, domain verification, and sandbox removal request; disproportionate complexity. |
| **openpyxl / xlsxwriter (local Excel files)** | Lose the real-time sharing and mobile access of Google Sheets; the end user needs a link they can check from their phone. |
| **gspread_asyncio** | Async is unnecessary for a batch job that runs once daily and makes 5 sequential API calls. Adds complexity with no benefit. |
| **Port 587 STARTTLS** | Works but port 465 SSL is more reliable from GitHub Actions runners; both are functionally equivalent for our use case. |

---

## 6. Dependency Summary

```
# requirements.txt (delivery-specific)
gspread>=6.0,<7.0
gspread-formatting>=1.2,<2.0
```

No email dependencies needed -- `smtplib` and `email.mime` are in the Python standard library.

---

## 7. Key Reference Links

- [gspread authentication docs](https://docs.gspread.org/en/latest/oauth2.html)
- [gspread Worksheet API](https://docs.gspread.org/en/latest/api/models/worksheet.html)
- [gspread-formatting (conditional formatting)](https://gspread-formatting.readthedocs.io/en/latest/)
- [Google Sheets API quota limits](https://developers.google.com/workspace/sheets/api/limits)
- [Google App Passwords](https://myaccount.google.com/apppasswords)
- [Gmail SMTP from GitHub Actions example](https://www.paulie.dev/posts/2025/02/how-to-send-email-using-github-actions/)
- [Base64 service account JSON in GitHub Actions](https://medium.com/@verazabeida/using-json-in-your-github-actions-when-authenticating-with-gcp-856089db28cf)
- [Python email examples (official docs)](https://docs.python.org/3/library/email.examples.html)
- [Resend pricing](https://resend.com/pricing)
- [Mailgun vs Resend comparison](https://www.sequenzy.com/versus/resend-vs-mailgun)
