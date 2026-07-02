"""
Edesy Vani -> Odoo Chatter Webhook Receiver
- Uses mail.message create to preserve HTML in chatter
- Creates follow-up Call activity when customer requests callback
"""

import os
import json
import logging
import xmlrpc.client
from datetime import date, timedelta
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
    """Write HTML log note to res.partner chatter via mail.message create."""
    db = db or ODOO_DB
    uid, models = get_odoo(db)
    msg_id = models.execute_kw(
        db, uid, ODOO_API_KEY,
        'mail.message', 'create',
        [{
            'body':         html_body,
            'message_type': 'comment',
            'model':        'res.partner',
            'res_id':       int(partner_id),
            'subtype_id':   2,      # mail.mt_note = internal log note
            'author_id':    uid,
        }]
    )
    return msg_id


def create_callback_activity(partner_id, summary, due_days=1, db=None):
    """
    Create a scheduled 'Call' activity on res.partner
    when the customer requests a callback.
    due_days=1 means the activity is due tomorrow by default.
    """
    db = db or ODOO_DB
    uid, models = get_odoo(db)
    due_date = (date.today() + timedelta(days=due_days)).strftime('%Y-%m-%d')
    activity_id = models.execute_kw(
        db, uid, ODOO_API_KEY,
        'mail.activity', 'create',
        [{
            'res_model':       'res.partner',
            'res_id':          int(partner_id),
            'activity_type_id': 2,   # Call
            'summary':         f'AI Call: Customer requested callback - {summary}',
            'note':            f'<p>Customer asked to be called back.<br/>{summary}</p>',
            'date_deadline':   due_date,
            'user_id':         uid,
        }]
    )
    _logger.info(f'Created callback activity {activity_id} on partner {partner_id}')
    return activity_id


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

    data      = payload.get('data', {})
    variables = data.get('variables', {})

    partner_id = variables.get('odoo_partner_id')
    if not partner_id:
        return jsonify({'error': 'Missing odoo_partner_id'}), 400

    duration   = int(data.get('call_duration', 0))
    transcript = data.get('transcript', '')
    summary    = data.get('summary', 'No summary available')
    outcome    = data.get('outcome', 'unknown')
    recording  = data.get('recording_url', '')
    call_sid   = data.get('call_sid', data.get('id', 'N/A'))
    mins, secs = divmod(duration, 60)

    customer_name      = variables.get('customer_name', '')
    outstanding_amount = variables.get('outstanding_amount', '')
    oldest_due_date    = variables.get('oldest_due_date', '')
    salesman_name      = variables.get('salesman_name', '')
    pdc_details        = variables.get('pdc_details', '')

    outcome_map = {
        'commitment_given':   '&#x2705; Payment commitment received',
        'dispute':            '&#x26A0;&#xFE0F; Customer disputed the amount',
        'callback_requested': '&#x1F501; Customer requested callback',
        'no_answer':          '&#x1F4F5; No answer',
        'voicemail':          '&#x1F4EC; Voicemail detected',
        'busy':               '&#x1F4F5; Line busy',
        'failed':             '&#x274C; Call failed',
    }
    outcome_label = outcome_map.get(outcome, outcome)
    rec_link = f'<a href="{recording}" target="_blank">&#x1F3D9; Listen to recording</a><br/>' if recording else ''
    ts = (transcript[:1200] + '...') if len(transcript) > 1200 else transcript

    # PDC row only if there are pending cheques
    pdc_row = (f'<tr><td style="padding:2px 12px 2px 0;color:#555"><b>PDC on record</b></td>'
               f'<td>{pdc_details}</td></tr>'
               if pdc_details and pdc_details != 'no pending PDC cheques' else '')

    body = f"""<div style="font-family:sans-serif;font-size:13px">
<b>&#x1F4DE; AI Payment Reminder &#x2014; Ria (Edesy Vani)</b><br/><br/>
<table style="border-collapse:collapse">
  <tr><td style="padding:2px 12px 2px 0;color:#555;width:150px"><b>Call ID</b></td><td>{call_sid}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Customer</b></td><td>{customer_name}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Outstanding</b></td><td>{outstanding_amount}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Oldest Due</b></td><td>{oldest_due_date}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Salesman</b></td><td>{salesman_name}</td></tr>
  {pdc_row}
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Duration</b></td><td>{mins}m {secs}s</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Outcome</b></td><td>{outcome_label}</td></tr>
  <tr><td style="padding:2px 12px 2px 0;color:#555"><b>Summary</b></td><td>{summary}</td></tr>
</table>
<br/>{rec_link}<hr style="border:none;border-top:1px solid #ddd;margin:8px 0"/>
<small style="color:#666"><b>Transcript:</b><br/>
<span style="white-space:pre-wrap;font-size:12px">{ts}</span>
</small></div>"""

    try:
        # 1. Write chatter note
        msg_id = post_html_note(partner_id, body)
        _logger.info(f'Posted message {msg_id} on partner {partner_id} ({customer_name})')

        # 2. If customer requested callback — create a scheduled Call activity
        activity_id = None
        if outcome == 'callback_requested':
            activity_id = create_callback_activity(
                partner_id = partner_id,
                summary    = summary,
                due_days   = 1   # due tomorrow; salesman will see it in activities
            )

        return jsonify({
            'status':      'ok',
            'message_id':  msg_id,
            'activity_id': activity_id,
            'partner_id':  int(partner_id)
        }), 200

    except Exception as e:
        _logger.error(f'Odoo error: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
