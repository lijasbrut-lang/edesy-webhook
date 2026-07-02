"""
Edesy Vani -> Odoo Chatter Webhook Receiver
Fix: use mail.message create directly to preserve HTML rendering in chatter
"""

import os
import json
import logging
import xmlrpc.client
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)

ODOO_URL     = os.environ.get('ODOO_URL', 'https://brutgroup.in')
ODOO_DB      = os.environ.get('ODOO_DB', 'odoo_db')
ODOO_USER    = os.environ.get('ODOO_USER', 'idealdestributors2025@gmail.com')
ODOO_API_KEY = os.environ.get('ODOO_API_KEY', '')


def get_odoo(db=None):
    db = db or ODOO_DB
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    uid    = common.authenticate(db, ODOO_USER, ODOO_API_KEY, {})
    models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
    return uid, models


def post_html_note(partner_id, html_body, db=None):
    db = db or ODOO_DB
    uid, models = get_odoo(db)
    msg_id = models.execute_kw(
        db, uid, ODOO_API_KEY,
        'mail.message', 'create',
        [{'body': html_body, 'message_type': 'comment', 'model': 'res.partner',
          'res_id': int(partner_id), 'subtype_id': 2, 'author_id': uid}]
    )
    return msg_id


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'edesy-odoo-webhook'}), 200


@app.route('/edesy/webhook', methods=['POST'])
def edesy_webhook():
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400

    _logger.info(f'Edesy webhook received: {json.dumps(payload)[:400]}')
    event_type = payload.get('type', '')
    if event_type != 'call.ended':
        return jsonify({'status': 'ignored', 'event': event_type}), 200

    data = payload.get('data', {})
    variables = data.get('variables', {})
    partner_id = variables.get('odoo_partner_id')
    if not partner_id:
        return jsonify({'error': 'Missing odoo_partner_id'}), 400

    duration  = int(data.get('call_duration', 0))
    transcript = data.get('transcript', '')
    summary   = data.get('summary', 'No summary available')
    outcome   = data.get('outcome', 'unknown')
    recording = data.get('recording_url', '')
    call_sid  = data.get('call_sid', data.get('id', 'N/A'))
    mins, secs = divmod(duration, 60)

    outcome_map = {
        'commitment_given': '&#x2705; Payment commitment received',
        'dispute': '&#x26A0;&#xFE0F; Customer disputed the amount',
        'callback_requested': '&#x1F501; Callback requested',
        'no_answer': '&#x1F4F5; No answer',
        'voicemail': '&#x1F4EC; Voicemail detected',
        'busy': '&#x1F4F5; Line busy',
        'failed': '&#x274C; Call failed',
    }
    outcome_label = outcome_map.get(outcome, outcome)
    rec_link = f'<a href="{recording}" target="_blank">&#x1F3D9; Listen</a><br/>' if recording else ''
    ts = (transcript[:1200] + '...') if len(transcript) > 1200 else transcript

    body = f"""<div style="font-family:sans-serif;font-size:13px">
<b>&#x1F4DE; AI Payment Reminder &#x2014; Ria (Edesy Vani)</b><br/><br/>
<table style="border-collapse:collapse">
  <tr><td style="padding:2px 12px 2px 0;color:#555;width:140px"><b>Call ID</b></td><td>{call_sid}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Customer</b></td><td>{variables.get('customer_name','')}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Outstanding</b></td><td>{variables.get('outstanding_amount','')}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Oldest Due</b></td><td>{variables.get('oldest_due_date','')}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Salesman</b></td><td>{variables.get('salesman_name','')}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Duration</b></td><td>{mins}m {secs}s</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Outcome</b></td><td>{outcome_label}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Summary</b></td><td>{summary}</td></tr>
</table>
<br/>{rec_link}<hr style="border:none;border-top:1px solid #ddd;margin:8px 0"/>
<small style="color:#666"><b>Transcript:</b><br/>
<span style="white-space:pre-wrap;font-size:12px">{ts}</span>
</small></div>"""

    try:
        msg_id = post_html_note(partner_id, body)
        _logger.info(f'Posted msg {msg_id} on partner {partner_id}')
        return jsonify({'status': 'ok', 'message_id': msg_id}), 200
    except Exception as e:
        _logger.error(f'Odoo error: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
