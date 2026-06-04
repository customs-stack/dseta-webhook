"""
DSETA Customs Clearance Webhook — Simplified
=============================================
Railway receives data from Zapier, calls Claude API,
returns instructions back to Zapier.
No Google Cloud service account needed.

Environment variables (set in Railway):
  ANTHROPIC_API_KEY - your Anthropic API key
"""

import os
import anthropic
from flask import Flask, request, jsonify
import datetime

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ── Get next DSETA reference ───────────────────────────────────────────────────
def get_next_reference(last_ref):
    """
    Zapier passes the last reference used.
    We increment it by 1.
    If none exists yet, start from DSETA/006.
    """
    if not last_ref or last_ref.strip() == "":
        return "DSETA/006"
    try:
        num = int(last_ref.replace("DSETA/", "").strip())
        return f"DSETA/{str(num + 1).zfill(3)}"
    except:
        return "DSETA/006"

# ── Use Claude to extract job data from email ──────────────────────────────────
def extract_job_data(from_email, subject, body):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""
You are a customs clearance assistant for DSETA Consulting Ltd.
A new clearance request email has arrived.

From: {from_email}
Subject: {subject}
Body: {body}

Extract the following and return as JSON only, no other text:
{{
  "services": "list services mentioned e.g. CDS, ENS, ELO or Unknown if not stated",
  "exporter": "exporter name and country if mentioned, else Unknown",
  "importer": "importer name if mentioned, else Unknown",
  "goods": "goods description if mentioned, else Unknown",
  "route": "route if mentioned e.g. Calais to Dover, else Unknown",
  "input_sheet": "a clean formatted text block with all above info ready to input into Descartes customs software"
}}
"""
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    import json
    try:
        return json.loads(message.content[0].text)
    except:
        return {
            "services": "Unknown",
            "exporter": "Unknown", 
            "importer": "Unknown",
            "goods": "Unknown",
            "route": "Unknown",
            "input_sheet": message.content[0].text
        }

# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 1 — New email received
# POST /new-email
#
# Zapier sends:
#   from_email, subject, body, date, last_reference
#
# We return:
#   reference, folder_name, input_sheet, auto_reply_body
#   (Zapier uses these to create folders, log sheet, send reply)
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/new-email", methods=["POST"])
def new_email():
    data       = request.json or {}
    from_email = data.get("from_email", "unknown@email.com")
    subject    = data.get("subject", "No subject")
    body       = data.get("body", "")
    date       = data.get("date", datetime.datetime.now().strftime("%d/%m/%Y"))
    last_ref   = data.get("last_reference", "")

    try:
        # Generate next reference
        ref = get_next_reference(last_ref)

        # Extract job data using Claude
        job_data = extract_job_data(from_email, subject, body)

        # Build folder name
        folder_name = f"{ref} — {from_email}"

        # Build input sheet text
        input_sheet = f"""DSETA CONSULTING — DESCARTES INPUT SHEET
{'='*50}
Reference:  {ref}
From:       {from_email}
Date:       {date}
Subject:    {subject}
{'='*50}
Services:   {job_data.get('services', 'Unknown')}
Exporter:   {job_data.get('exporter', 'Unknown')}
Importer:   {job_data.get('importer', 'Unknown')}
Goods:      {job_data.get('goods', 'Unknown')}
Route:      {job_data.get('route', 'Unknown')}
{'='*50}
{job_data.get('input_sheet', '')}
{'='*50}
Generated: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
"""

        # Build auto-reply
        auto_reply = f"""Dear Customer,

Thank you for contacting DSETA Consulting Ltd.

We have received your documents and your clearance request has been logged under reference: {ref}

Please quote this reference in any future correspondence.

Services noted: {job_data.get('services', 'To be confirmed')}

We will begin processing your declaration and will update you once complete.

Kind regards,
DSETA Consulting Ltd
customs@dseta.co.uk"""

        return jsonify({
            "status":        "success",
            "reference":     ref,
            "folder_name":   folder_name,
            "from_email":    from_email,
            "services":      job_data.get("services", "Unknown"),
            "input_sheet":   input_sheet,
            "auto_reply":    auto_reply,
            "date":          date
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 2 — Job completed (file uploaded to completed folder)
# POST /job-completed
#
# Zapier sends:
#   folder_name, from_email, reference
#
# We return:
#   completion_email_body
#   (Zapier sends this to the customer)
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/job-completed", methods=["POST"])
def job_completed():
    data        = request.json or {}
    folder_name = data.get("folder_name", "")
    from_email  = data.get("from_email", "")
    reference   = data.get("reference", "")
    files       = data.get("files", "your clearance documents")

    completion_email = f"""Dear Customer,

Your customs clearance has been completed successfully.

Reference: {reference}

Please find your completed documents attached to this email.

If you have any questions regarding your clearance, please do not hesitate to contact us.

Kind regards,
DSETA Consulting Ltd
customs@dseta.co.uk"""

    return jsonify({
        "status":           "success",
        "reference":        reference,
        "from_email":       from_email,
        "completion_email": completion_email
    }), 200


# ── Health check ───────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "DSETA webhook running ✅", "version": "2.0"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
