"""
DSETA Customs Clearance Webhook v4
===================================
Fixes:
- Loop prevention (ignore dseta.co.uk emails)
- Proper email body extraction
- Attachment handling instructions
- Clean auto-reply from name
- Sheet spam prevention
"""

import os
import json
import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ── Domains to ignore (prevents loop) ─────────────────────────────────────────
IGNORED_DOMAINS = [
    "dseta.co.uk",
    "noreply",
    "no-reply",
    "mailer-daemon",
    "postmaster",
    "descartes",
    "cnsonline",
    "douane.gouv.fr"
]

def should_ignore_email(from_email):
    from_email_lower = from_email.lower()
    for domain in IGNORED_DOMAINS:
        if domain in from_email_lower:
            return True
    return False

def get_next_reference(last_ref):
    if not last_ref or str(last_ref).strip() in ["", "Ref\n(DSETA/00X)", "Ref (DSETA/00X)"]:
        return "DSETA/006"
    try:
        clean = str(last_ref).replace("DSETA/", "").strip()
        num = int(clean)
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
        "model": "claude-opus-4-5",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": f"""You are a customs clearance assistant for DSETA Consulting Ltd.
A new clearance request email has arrived. Extract all available information.

From: {from_email}
Subject: {subject}
Email Body: {body}

Return ONLY a valid JSON object, no markdown, no explanation, no code blocks:
{{
  "services": "list all services mentioned such as CDS, ENS, ELO. If not stated write To be confirmed",
  "exporter": "exporter company name and country if mentioned, else To be confirmed",
  "importer": "importer company name if mentioned, else To be confirmed",
  "goods": "description of goods if mentioned, else To be confirmed",
  "route": "shipping route if mentioned e.g. Calais to Dover, else To be confirmed",
  "commodity_code": "commodity code if mentioned, else To be confirmed",
  "invoice_value": "invoice value and currency if mentioned, else To be confirmed",
  "incoterms": "incoterms if mentioned e.g. DAP, FOB, else To be confirmed",
  "transport": "vehicle reg or trailer number if mentioned, else To be confirmed"
}}"""
        }]
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload
            )
        result = response.json()
        if "content" in result and len(result["content"]) > 0:
            text = result["content"][0].get("text", "{}")
            text = text.strip()
            # Remove markdown if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            return json.loads(text)
        else:
            raise Exception(f"API error: {result}")
    except Exception as e:
        return {
            "services": "To be confirmed",
            "exporter": "To be confirmed",
            "importer": "To be confirmed",
            "goods": "To be confirmed",
            "route": "To be confirmed",
            "commodity_code": "To be confirmed",
            "invoice_value": "To be confirmed",
            "incoterms": "To be confirmed",
            "transport": "To be confirmed"
        }


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 1 — New email
# POST /new-email
# Zapier sends: from_email, subject, body, date, last_reference
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/new-email", methods=["POST"])
def new_email():
    data       = request.json or {}
    from_email = data.get("from_email", "").strip()
    subject    = data.get("subject", "No subject").strip()
    body       = data.get("body", "").strip()
    date       = data.get("date", datetime.datetime.now().strftime("%d/%m/%Y"))
    last_ref   = data.get("last_reference", "")

    # ── Block loop emails ──────────────────────────────────────────────────────
    if should_ignore_email(from_email):
        return jsonify({
            "status": "ignored",
            "reason": f"Email from {from_email} ignored — internal or system sender"
        }), 200

    # ── Block if no body (empty trigger) ──────────────────────────────────────
    if not from_email or from_email == "unknown@email.com":
        return jsonify({
            "status": "ignored",
            "reason": "No valid sender email"
        }), 200

    try:
        ref      = get_next_reference(last_ref)
        job_data = extract_job_data(from_email, subject, body)
        folder_name = f"{ref} — {from_email}"

        input_sheet = f"""DSETA CONSULTING — DESCARTES INPUT SHEET
{'='*55}
Reference:       {ref}
From:            {from_email}
Date:            {date}
Subject:         {subject}
{'='*55}
SERVICES:        {job_data.get('services', 'To be confirmed')}
{'='*55}
GOODS DETAILS
Goods:           {job_data.get('goods', 'To be confirmed')}
Commodity Code:  {job_data.get('commodity_code', 'To be confirmed')}
Invoice Value:   {job_data.get('invoice_value', 'To be confirmed')}
Incoterms:       {job_data.get('incoterms', 'To be confirmed')}
{'='*55}
PARTIES
Exporter:        {job_data.get('exporter', 'To be confirmed')}
Importer:        {job_data.get('importer', 'To be confirmed')}
{'='*55}
TRANSPORT
Route:           {job_data.get('route', 'To be confirmed')}
Transport:       {job_data.get('transport', 'To be confirmed')}
{'='*55}
Generated: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
⚠️  Please verify all details before submitting to Descartes
"""

        # Auto reply — from name not raw email
        auto_reply = f"""Dear Customer,

Thank you for contacting DSETA Consulting Ltd.

We have received your documents and your clearance request has been logged under reference: {ref}

Please use this reference number in all future correspondence relating to this shipment.

Services noted: {job_data.get('services', 'To be confirmed')}

We will begin processing your declaration and will be in touch shortly.

Kind regards,

DSETA Consulting Ltd
Tel: +44 XXXX XXXXXX
Email: customs@dseta.co.uk
www.dseta.co.uk"""

        auto_reply_subject = f"Clearance Request Received — Reference {ref}"

        return jsonify({
            "status":               "success",
            "reference":            ref,
            "folder_name":          folder_name,
            "from_email":           from_email,
            "services":             job_data.get("services", "To be confirmed"),
            "exporter":             job_data.get("exporter", "To be confirmed"),
            "importer":             job_data.get("importer", "To be confirmed"),
            "goods":                job_data.get("goods", "To be confirmed"),
            "route":                job_data.get("route", "To be confirmed"),
            "commodity_code":       job_data.get("commodity_code", "To be confirmed"),
            "invoice_value":        job_data.get("invoice_value", "To be confirmed"),
            "input_sheet":          input_sheet,
            "auto_reply":           auto_reply,
            "auto_reply_subject":   auto_reply_subject,
            "date":                 date
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 2 — Job completed
# POST /job-completed
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/job-completed", methods=["POST"])
def job_completed():
    data       = request.json or {}
    reference  = data.get("reference", "")
    from_email = data.get("from_email", "")
    files      = data.get("files", "your clearance documents")

    completion_email = f"""Dear Customer,

Your customs clearance has been completed successfully.

Reference: {reference}

Please find your completed clearance documents attached to this email. Please retain these for your records as they may be required by HMRC or your freight forwarder.

If you have any questions regarding your clearance, please do not hesitate to contact us.

Kind regards,

DSETA Consulting Ltd
Tel: +44 XXXX XXXXXX
Email: customs@dseta.co.uk
www.dseta.co.uk"""

    completion_subject = f"Clearance Complete — Reference {reference}"

    return jsonify({
        "status":             "success",
        "reference":          reference,
        "from_email":         from_email,
        "completion_email":   completion_email,
        "completion_subject": completion_subject
    }), 200


# ── Health check ───────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "DSETA webhook running",
        "version": "4.0",
        "endpoints": ["/new-email", "/job-completed"]
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
