"""
DSETA Customs Clearance Webhook v5
===================================
Full extraction from email body + attachments
"""

import os
import json
import datetime
import base64
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

IGNORED_DOMAINS = [
    "dseta.co.uk", "noreply", "no-reply",
    "mailer-daemon", "postmaster", "descartes",
    "cnsonline", "douane.gouv.fr"
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

def download_attachment(url):
    """Download attachment from URL and return base64 encoded content"""
    import httpx
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code == 200:
                return base64.standard_b64encode(response.content).decode("utf-8")
    except:
        pass
    return None

def extract_all_data(from_email, subject, body, attachment_urls=None):
    """Extract data from email body + attachments using Claude"""
    import httpx

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    # Build message content — start with email text
    content = []

    # Add attachment PDFs if available
    if attachment_urls:
        for url_info in attachment_urls:
            url = url_info.get("url", "") if isinstance(url_info, dict) else str(url_info)
            filename = url_info.get("filename", "attachment") if isinstance(url_info, dict) else "attachment"
            if url:
                file_data = download_attachment(url)
                if file_data:
                    # Determine media type
                    if filename.lower().endswith(".pdf"):
                        content.append({
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": file_data
                            }
                        })
                    else:
                        # For images
                        content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": file_data
                            }
                        })

    # Add the text prompt
    content.append({
        "type": "text",
        "text": f"""You are a UK customs clearance expert for DSETA Consulting Ltd.
Extract ALL available information from this email and any attached documents.

EMAIL FROM: {from_email}
SUBJECT: {subject}
EMAIL BODY:
{body}

Extract every piece of information you can find and return ONLY a valid JSON object:
{{
  "services_requested": "List all services customer requested e.g. CDS, ENS, ELO, T1. If not stated write To be confirmed",
  "exporter_name": "Full exporter company name",
  "exporter_address": "Full exporter address including country",
  "exporter_eori": "Exporter EORI number if found",
  "importer_name": "Full importer company name",
  "importer_address": "Full importer address",
  "importer_eori": "Importer EORI number if found",
  "goods_description": "Full description of goods",
  "commodity_code": "HS/commodity code",
  "country_of_origin": "Country of origin",
  "preferential_origin": "Preferential origin / REX number if found",
  "number_of_packages": "Number and type of packages e.g. 20 pallets",
  "gross_weight": "Gross weight in kg",
  "net_weight": "Net weight in kg",
  "invoice_number": "Invoice reference number",
  "invoice_value": "Invoice value with currency",
  "incoterms": "Incoterms e.g. DAP, FOB, CIF",
  "freight_cost": "Freight cost if mentioned",
  "route": "Route e.g. Calais to Dover",
  "transport_mode": "Transport mode e.g. RoRo, Accompanied, Unaccompanied",
  "vehicle_reg": "Vehicle or trailer registration if found",
  "carrier": "Carrier or haulier name if found",
  "health_cert": "Health certificate number if found",
  "additional_notes": "Any other relevant information"
}}
For any field not found write: To be confirmed"""
    })

    payload = {
        "model": "claude-opus-4-5",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": content}]
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload
            )
        result = response.json()
        if "content" in result and len(result["content"]) > 0:
            text = result["content"][0].get("text", "{}")
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            return json.loads(text)
        else:
            raise Exception(f"API error: {result}")
    except Exception as e:
        return {field: "To be confirmed" for field in [
            "services_requested","exporter_name","exporter_address",
            "exporter_eori","importer_name","importer_address","importer_eori",
            "goods_description","commodity_code","country_of_origin",
            "preferential_origin","number_of_packages","gross_weight",
            "net_weight","invoice_number","invoice_value","incoterms",
            "freight_cost","route","transport_mode","vehicle_reg",
            "carrier","health_cert","additional_notes"
        ]}

def build_input_sheet(ref, from_email, date, subject, d):
    """Build the formatted Descartes input sheet"""
    return f"""DSETA CONSULTING LTD — CUSTOMS CLEARANCE INPUT SHEET
{'='*60}
REFERENCE:        {ref}
DATE RECEIVED:    {date}
FROM:             {from_email}
SUBJECT:          {subject}
{'='*60}

SERVICES REQUESTED
{'─'*60}
{d.get('services_requested', 'To be confirmed')}

{'='*60}
EXPORTER DETAILS
{'─'*60}
Name:             {d.get('exporter_name', 'To be confirmed')}
Address:          {d.get('exporter_address', 'To be confirmed')}
EORI:             {d.get('exporter_eori', 'To be confirmed')}

IMPORTER DETAILS
{'─'*60}
Name:             {d.get('importer_name', 'To be confirmed')}
Address:          {d.get('importer_address', 'To be confirmed')}
EORI:             {d.get('importer_eori', 'To be confirmed')}

{'='*60}
GOODS DETAILS
{'─'*60}
Description:      {d.get('goods_description', 'To be confirmed')}
Commodity Code:   {d.get('commodity_code', 'To be confirmed')}
Country of Origin:{d.get('country_of_origin', 'To be confirmed')}
Preferential/REX: {d.get('preferential_origin', 'To be confirmed')}
Packages:         {d.get('number_of_packages', 'To be confirmed')}
Gross Weight:     {d.get('gross_weight', 'To be confirmed')}
Net Weight:       {d.get('net_weight', 'To be confirmed')}

{'='*60}
FINANCIAL DETAILS
{'─'*60}
Invoice Number:   {d.get('invoice_number', 'To be confirmed')}
Invoice Value:    {d.get('invoice_value', 'To be confirmed')}
Incoterms:        {d.get('incoterms', 'To be confirmed')}
Freight Cost:     {d.get('freight_cost', 'To be confirmed')}

{'='*60}
TRANSPORT DETAILS
{'─'*60}
Route:            {d.get('route', 'To be confirmed')}
Transport Mode:   {d.get('transport_mode', 'To be confirmed')}
Vehicle/Trailer:  {d.get('vehicle_reg', 'To be confirmed')}
Carrier:          {d.get('carrier', 'To be confirmed')}

{'='*60}
DOCUMENTS & CERTIFICATES
{'─'*60}
Health Cert:      {d.get('health_cert', 'To be confirmed')}

{'='*60}
ADDITIONAL NOTES
{'─'*60}
{d.get('additional_notes', 'None')}

{'='*60}
Generated: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
⚠️  Verify all details before submitting to Descartes
"""


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 1 — New email
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/new-email", methods=["POST"])
def new_email():
    data             = request.json or {}
    from_email       = data.get("from_email", "").strip()
    subject          = data.get("subject", "No subject").strip()
    body             = data.get("body", "").strip()
    date             = data.get("date", datetime.datetime.now().strftime("%d/%m/%Y"))
    last_ref         = data.get("last_reference", "")
    attachment_urls  = data.get("attachments", [])

    # Block loop/system emails
    if should_ignore_email(from_email) or not from_email:
        return jsonify({"status": "ignored", "reason": f"Ignored: {from_email}"}), 200

    try:
        ref      = get_next_reference(last_ref)
        job_data = extract_all_data(from_email, subject, body, attachment_urls)
        folder_name  = f"{ref} — {from_email}"
        input_sheet  = build_input_sheet(ref, from_email, date, subject, job_data)

        auto_reply_subject = f"Clearance Request Received — Reference {ref}"
        auto_reply = f"""Dear Customer,

Thank you for contacting DSETA Consulting Ltd.

We have received your documents and your clearance request has been logged under reference: {ref}

Please use this reference number in all future correspondence relating to this shipment.

Services noted: {job_data.get('services_requested', 'To be confirmed')}

We will begin processing your declaration and will be in touch shortly.

Kind regards,

DSETA Consulting Ltd
Tel: +44 XXXX XXXXXX
Email: customs@dseta.co.uk
www.dseta.co.uk"""

        return jsonify({
            "status":             "success",
            "reference":          ref,
            "folder_name":        folder_name,
            "from_email":         from_email,
            "services":           job_data.get("services_requested", "To be confirmed"),
            "exporter":           job_data.get("exporter_name", "To be confirmed"),
            "importer":           job_data.get("importer_name", "To be confirmed"),
            "goods":              job_data.get("goods_description", "To be confirmed"),
            "commodity_code":     job_data.get("commodity_code", "To be confirmed"),
            "invoice_value":      job_data.get("invoice_value", "To be confirmed"),
            "route":              job_data.get("route", "To be confirmed"),
            "input_sheet":        input_sheet,
            "auto_reply":         auto_reply,
            "auto_reply_subject": auto_reply_subject,
            "date":               date
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE 2 — Job completed
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/job-completed", methods=["POST"])
def job_completed():
    data       = request.json or {}
    reference  = data.get("reference", "")
    from_email = data.get("from_email", "")

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

    return jsonify({
        "status":             "success",
        "reference":          reference,
        "from_email":         from_email,
        "completion_email":   completion_email,
        "completion_subject": f"Clearance Complete — Reference {reference}"
    }), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "DSETA webhook running", "version": "5.0"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
