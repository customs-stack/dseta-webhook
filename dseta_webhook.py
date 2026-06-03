"""
DSETA Customs Clearance Webhook
================================
Sits between Zapier and Claude API.
Handles two triggers:
  1. New email → create folders, log sheet, auto-reply
  2. New file in Completed folder → email customer with all docs

Environment variables required (set in Railway):
  ANTHROPIC_API_KEY   - your Anthropic API key
  GOOGLE_SHEET_ID     - 1ftmJQv6WM0JvL5hcOW5QgJqekj6DzhJmrFVm9Sp2JFk
  CUSTOMERS_FOLDER_ID - 17MZrIesy2qRzePkh9w1bi633BBECuNeq
  GOOGLE_CREDS_JSON   - your Google service account JSON (see setup guide)
"""

import os
import json
import base64
import anthropic
from flask import Flask, request, jsonify
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import io
import datetime

app = Flask(__name__)

# ── Config from environment variables ─────────────────────────────────────────
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_SHEET_ID     = os.environ.get("GOOGLE_SHEET_ID", "1ftmJQv6WM0JvL5hcOW5QgJqekj6DzhJmrFVm9Sp2JFk")
CUSTOMERS_FOLDER_ID = os.environ.get("CUSTOMERS_FOLDER_ID", "17MZrIesy2qRzePkh9w1bi633BBECuNeq")
GOOGLE_CREDS_JSON   = os.environ.get("GOOGLE_CREDS_JSON")
CUSTOMS_EMAIL       = "customs@dseta.co.uk"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
]

# ── Google auth ────────────────────────────────────────────────────────────────
def get_google_services():
    creds_info = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    drive   = build("drive",   "v3", credentials=creds)
    sheets  = build("sheets",  "v4", credentials=creds)
    gmail   = build("gmail",   "v1", credentials=creds)
    return drive, sheets, gmail

# ── Get next DSETA reference ───────────────────────────────────────────────────
def get_next_reference(sheets):
    result = sheets.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range="Jobs!A:A"
    ).execute()
    values = result.get("values", [])
    # Find highest DSETA number
    max_num = 5  # start from 006
    for row in values:
        if row and row[0].startswith("DSETA/"):
            try:
                num = int(row[0].replace("DSETA/", ""))
                if num > max_num:
                    max_num = num
            except:
                pass
    next_num = max_num + 1
    return f"DSETA/{str(next_num).zfill(3)}"

# ── Create Drive folder ────────────────────────────────────────────────────────
def create_folder(drive, name, parent_id):
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder.get("id")

# ── Log row in Google Sheet ────────────────────────────────────────────────────
def log_to_sheet(sheets, ref, from_email, date, subject):
    row = [ref, "", from_email, date, subject, "", "", "", "", "", "", "", "", "", "", "Pending", "", ""]
    sheets.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range="Jobs!A:R",
        valueInputOption="RAW",
        body={"values": [row]}
    ).execute()
    # Also log to Pricing sheet
    pricing_row = [ref, "", from_email, date, subject, "", "", "", "", "", "Pending", ""]
    sheets.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range="Pricing!A:L",
        valueInputOption="RAW",
        body={"values": [pricing_row]}
    ).execute()

# ── Send email via Gmail API ───────────────────────────────────────────────────
def send_email(gmail, to, subject, body, attachments=None):
    msg = MIMEMultipart()
    msg["to"]      = to
    msg["from"]    = CUSTOMS_EMAIL
    msg["subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if attachments:
        for filename, data in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()

# ── List all files in a folder ─────────────────────────────────────────────────
def list_files_in_folder(drive, folder_id):
    results = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, mimeType)"
    ).execute()
    return results.get("files", [])

# ── Download file from Drive ───────────────────────────────────────────────────
def download_file(drive, file_id):
    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()

# ── Use Claude to extract Descartes input data ─────────────────────────────────
def extract_descartes_data(from_email, subject, body):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""
You are a customs clearance assistant for DSETA Consulting Ltd.
A new clearance request has arrived by email.

From: {from_email}
Subject: {subject}
Body: {body}

Extract the following information if mentioned:
- Services requested (CDS, ENS, ELO)
- Exporter name and country
- Importer name
- Goods description
- Route (e.g. Calais to Dover)
- Any reference numbers mentioned

Format as a clean text block labelled "DESCARTES INPUT SHEET".
If information is not available, write "To be confirmed".
Keep it concise and professional.
"""
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 1 — New email received (Zap 1)
# POST /new-email
# Zapier sends: from_email, subject, body, date
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/new-email", methods=["POST"])
def new_email():
    data        = request.json
    from_email  = data.get("from_email", "unknown@email.com")
    subject     = data.get("subject", "No subject")
    body        = data.get("body", "")
    date        = data.get("date", datetime.datetime.now().strftime("%d/%m/%Y"))

    try:
        drive, sheets, gmail = get_google_services()

        # 1. Get next reference
        ref = get_next_reference(sheets)

        # 2. Create customer folder structure
        # Check if customer folder already exists
        existing = drive.files().list(
            q=f"name='{from_email}' and '{CUSTOMERS_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id)"
        ).execute()

        if existing.get("files"):
            customer_folder_id = existing["files"][0]["id"]
        else:
            customer_folder_id = create_folder(drive, from_email, CUSTOMERS_FOLDER_ID)

        # 3. Create Pending subfolder for this reference
        pending_folder_id = create_folder(drive, f"{ref} — Pending", customer_folder_id)

        # 4. Create Completed subfolder for this reference (empty, ready for you)
        create_folder(drive, f"{ref} — Completed", customer_folder_id)

        # 5. Extract Descartes data using Claude
        descartes_data = extract_descartes_data(from_email, subject, body)

        # 6. Write INPUT.txt to Pending folder
        input_content = f"""DSETA CONSULTING — DESCARTES INPUT SHEET
Reference: {ref}
From: {from_email}
Date: {date}
Subject: {subject}
{'='*50}

{descartes_data}

{'='*50}
Generated automatically by DSETA automation system
"""
        input_file_meta = {
            "name": f"{ref}_INPUT.txt",
            "parents": [pending_folder_id],
            "mimeType": "text/plain"
        }
        from googleapiclient.http import MediaInMemoryUpload
        media = MediaInMemoryUpload(input_content.encode(), mimetype="text/plain")
        drive.files().create(body=input_file_meta, media_body=media).execute()

        # 7. Log to Google Sheet
        log_to_sheet(sheets, ref, from_email, date, subject)

        # 8. Send auto-reply to customer
        reply_body = f"""Dear Customer,

Thank you for contacting DSETA Consulting Ltd.

We have received your documents and your clearance request has been logged under reference: {ref}

Please quote this reference in any future correspondence.

We will begin processing your declaration and will update you once complete.

Kind regards,
DSETA Consulting Ltd
customs@dseta.co.uk
"""
        send_email(gmail, from_email, f"Re: {subject} — Reference {ref}", reply_body)

        return jsonify({
            "status": "success",
            "reference": ref,
            "pending_folder": pending_folder_id,
            "message": f"Job {ref} created successfully"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 2 — Completed docs uploaded (Zap 2)
# POST /job-completed
# Zapier sends: folder_name (e.g. "DSETA/006 — Completed"), folder_id
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/job-completed", methods=["POST"])
def job_completed():
    data        = request.json
    folder_name = data.get("folder_name", "")
    folder_id   = data.get("folder_id", "")

    # Extract reference from folder name e.g. "DSETA/006 — Completed" → "DSETA/006"
    ref = folder_name.replace(" — Completed", "").strip()

    # Extract customer email — folder structure is:
    # Customers / customer@email.com / DSETA006 — Completed
    # We need to get the parent folder name (customer email)
    try:
        drive, sheets, gmail = get_google_services()

        # Get parent folder (customer email folder)
        folder_meta = drive.files().get(
            fileId=folder_id,
            fields="parents"
        ).execute()
        parent_id = folder_meta["parents"][0]
        parent_meta = drive.files().get(
            fileId=parent_id,
            fields="name"
        ).execute()
        customer_email = parent_meta["name"]

        # List all files in Completed folder
        files = list_files_in_folder(drive, folder_id)

        if not files:
            return jsonify({"status": "error", "message": "No files found in completed folder"}), 400

        # Download all files
        attachments = []
        for f in files:
            if not f["name"].endswith(".txt"):  # skip INPUT.txt
                file_data = download_file(drive, f["id"])
                attachments.append((f["name"], file_data))

        # Update tracker — find row and mark Completed
        result = sheets.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Jobs!A:A"
        ).execute()
        values = result.get("values", [])
        row_num = None
        for i, row in enumerate(values):
            if row and row[0] == ref:
                row_num = i + 1
                break

        if row_num:
            today = datetime.datetime.now().strftime("%d/%m/%Y")
            # Update CDS Status (col L) and Processed Date (col Q)
            sheets.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"Jobs!L{row_num}",
                valueInputOption="RAW",
                body={"values": [["Completed"]]}
            ).execute()
            sheets.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"Jobs!Q{row_num}",
                valueInputOption="RAW",
                body={"values": [[today]]}
            ).execute()

        # Send email to customer with all attachments
        email_body = f"""Dear Customer,

Your customs clearance has been completed successfully.

Reference: {ref}

Please find your documents attached to this email.

If you have any questions, please do not hesitate to contact us.

Kind regards,
DSETA Consulting Ltd
customs@dseta.co.uk
"""
        send_email(
            gmail,
            customer_email,
            f"Your Clearance is Complete — {ref}",
            email_body,
            attachments=attachments
        )

        return jsonify({
            "status":   "success",
            "reference": ref,
            "customer": customer_email,
            "files_sent": len(attachments),
            "message":  f"Completion email sent to {customer_email} with {len(attachments)} attachment(s)"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Health check ───────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "DSETA webhook running ✅"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
