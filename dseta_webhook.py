"""
DSETA Customs Clearance Webhook — Fixed
"""

import os
import json
import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

def get_next_reference(last_ref):
    if not last_ref or str(last_ref).strip() == "":
        return "DSETA/006"
    try:
        num = int(str(last_ref).replace("DSETA/", "").strip())
        return f"DSETA/{str(num + 1).zfill(3)}"
    except:
        return "DSETA/006"

def extract_job_data(from_email, subject, body):
    import httpx
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": f"""You are a customs clearance assistant for DSETA Consulting Ltd.
A new clearance request email has arrived.

From: {from_email}
Subject: {subject}
Body: {body}

Extract the following and return as JSON only, no other text, no markdown:
{{
  "services": "list services mentioned e.g. CDS, ENS, ELO or Unknown if not stated",
  "exporter": "exporter name and country if mentioned, else Unknown",
  "importer": "importer name if mentioned, else Unknown",
  "goods": "goods description if mentioned, else Unknown",
  "route": "route if mentioned e.g. Calais to Dover, else Unknown",
  "input_sheet": "a clean formatted text block with all above info ready to input into Descartes customs software"
}}"""
        }]
    }
    
    with httpx.Client() as client:
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
            timeout=30.0
        )
    
    result = response.json()
    text = result["content"][0]["text"]
    
    try:
        return json.loads(text)
    except:
        return {
            "services": "Unknown",
            "exporter": "Unknown",
            "importer": "Unknown",
            "goods": "Unknown",
            "route": "Unknown",
            "input_sheet": text
        }

@app.route("/new-email", methods=["POST"])
def new_email():
    data       = request.json or {}
    from_email = data.get("from_email", "unknown@email.com")
    subject    = data.get("subject", "No subject")
    body       = data.get("body", "")
    date       = data.get("date", datetime.datetime.now().strftime("%d/%m/%Y"))
    last_ref   = data.get("last_reference", "")

    try:
        ref      = get_next_reference(last_ref)
        job_data = extract_job_data(from_email, subject, body)

        folder_name = f"{ref} — {from_email}"

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
            "status":      "success",
            "reference":   ref,
            "folder_name": folder_name,
            "from_email":  from_email,
            "services":    job_data.get("services", "Unknown"),
            "input_sheet": input_sheet,
            "auto_reply":  auto_reply,
            "date":        date
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/job-completed", methods=["POST"])
def job_completed():
    data      = request.json or {}
    reference = data.get("reference", "")
    from_email = data.get("from_email", "")

    completion_email = f"""Dear Customer,

Your customs clearance has been completed successfully.

Reference: {reference}

Please find your completed documents attached to this email.

If you have any questions, please do not hesitate to contact us.

Kind regards,
DSETA Consulting Ltd
customs@dseta.co.uk"""

    return jsonify({
        "status":           "success",
        "reference":        reference,
        "from_email":       from_email,
        "completion_email": completion_email
    }), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "DSETA webhook running", "version": "3.0"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
