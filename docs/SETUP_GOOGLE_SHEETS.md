# Google Sheets & Email Setup Guide

This guide walks you through setting up the Google Sheets integration and Gmail email delivery for NadiaAI. Total time: ~15 minutes.

## 1. Create a Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one) — name it `nadiaai`
3. Enable the **Google Sheets API**:
   - Go to *APIs & Services > Library*
   - Search for "Google Sheets API"
   - Click **Enable**
4. Enable the **Google Drive API** (same steps, search "Google Drive API")
5. Create a service account:
   - Go to *APIs & Services > Credentials*
   - Click **Create Credentials > Service Account**
   - Name: `nadiaai-sheets`
   - Click **Create and Continue** (skip optional steps)
   - Click **Done**
6. Create a key:
   - Click on the service account you just created
   - Go to the **Keys** tab
   - Click **Add Key > Create New Key > JSON**
   - Save the downloaded JSON file somewhere safe (e.g., `~/.config/nadiaai/service-account.json`)
   - **NEVER commit this file to git**

Note the service account email — it looks like: `nadiaai-sheets@your-project.iam.gserviceaccount.com`

## 2. Set Up the Google Sheet

1. Go to [Google Sheets](https://sheets.google.com) and create a new spreadsheet
2. Name it: **NadiaAI Leads**
3. Share it with the service account email from step 1 — give it **Editor** access
4. Copy the Sheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/THIS_IS_THE_SHEET_ID/edit
   ```
5. Share the sheet with Nadia's Google account too (Editor access)

## 3. Set Up Gmail App Password

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already enabled
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Create a new app password:
   - App: *Mail*
   - Device: *Other (Custom name)* → enter `NadiaAI`
5. Copy the 16-character app password (looks like: `xxxx xxxx xxxx xxxx`)

## 4. Configure .env

Copy `.env.example` to `.env` and fill in:

```bash
cp .env.example .env
```

```
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json
LEADS_SHEET_ID=your-sheet-id-from-step-2
SMTP_USER=your.gmail@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx
MAMA_EMAIL=nadia@example.com
```

## 5. Test It

```bash
# Install
pip install -e .

# Run the pipeline
python -m nadia_ai
```

You should see:
- A new `Leads` tab created in the Google Sheet with headers
- An email sent to Nadia's address

## 6. GitHub Actions Secrets (for daily cron)

For the daily automated run, add these secrets to your GitHub repo:

1. Go to repo *Settings > Secrets and variables > Actions*
2. Add these secrets:

| Secret Name | Value |
|------------|-------|
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | Base64-encoded service account JSON (see below) |
| `LEADS_SHEET_ID` | Your Google Sheet ID |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASSWORD` | Your Gmail app password |
| `MAMA_EMAIL` | Nadia's email address |
| `DEV_ALERT_EMAIL` | Your email (optional, for failure alerts) |

To base64-encode the service account JSON:

```bash
base64 -w 0 < service-account.json
```

On macOS:
```bash
base64 -i service-account.json
```

Copy the output and paste it as the `GOOGLE_SERVICE_ACCOUNT_JSON_B64` secret.
