"""
Edesy Vani -> Odoo Chatter Webhook Receiver
Deployed on Render (trial) - move to Odoo server later
"""

import os
import json
import hmac
import hashlib
import logging
import xmlrpc.client
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)

ODOO_URL             = os.environ.get('ODOO_URL', 'https://brutgroup.in')
ODOO_DB              = os.environ.get('ODOO_DB', 'odoo_db')
ODOO_USER            = os.environ.get('ODOO_USER', 'idealdestributors2025@gmail.com')
ODOO_API_KEY         = os.environ.get('ODOO_API_KEY', '')
EDESY_WEBHOOK_SECRET = os.environ.get('EDESY_WEBHOOK_SECRET', '')


def get_odoo_connection():
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    uid    = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
    models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
    return uid, models


def verify_signature(payload_bytes, sig_header, secret):
    if not secret:
        return True
    try:
        parts     = dict(p.split('=', 1) for p in sig_header.split(','))
        timestamp = parts.get('t', '')
        signature = parts.get('v1', '')
        signed    = f"{timestamp}.{payload_bytes.decode('utf-8')}"
        expected  = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        _logger.error(f'Signature verification error: {e}')
        return False


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'edesy-odoo-webhook'}), 200


@app.route('/edesy/webhook', methods=['POST'])
def edesy_webhook():
    sig_header = request.headers.get('X-Webhook-Signature', '')
    if EDESY_WEBHOOK_SECRET and not verify_signature(request.data, sig_header, EDESY_WEBHOOK_SECRET):
        _logger.warning('Edesy webhook: invalid signature rejected')
        return jsonify({'error': 'Invalid signature'}), 401

    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400

    _logger.info(f'Edesy webhook received: {json.dumps(payload)[:500]}')

    event_type = payload.get('type', '')
    if event_type != 'call.ended':
        return jsonify({'status': 'ignored', 'event': event_type}), 200

    data      = payload.get('data', {})
    variables = data.get('variables', {})

    partner_id = variables.get('odoo_partner_id')
    if not partner_id:
        _logger.error('No odoo_partner_id in webhook payload')
        return jsonify({'error': 'Missing odoo_partner_id'}), 400

    try:
        partner_id = int(partner_id)
    except ValueError:
        return jsonify({'error': 'Invalid odoo_partner_id'}), 400

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

    outcome_map = {
        'commitment_given':   'Payment commitment received',
        'dispute':            'Customer disputed the amount',
        'callback_requested': 'Callback requested',
        'no_answer':          'No answer',
        'voicemail':          'Voicemail detected',
        'busy':               'Line busy',
        'failed':             'Call failed',
    }
    outcome_label = outcome_map.get(outcome, outcome)

    rec_link = f'<a href="{recording}" target="_blank">Listen to recording</a><br/>' if recording else ''
    transcript_snippet = (transcript[:1200] + '...') if len(transcript) > 1200 else transcript

    body = f"""
        <div style="font-family:sans-serif;font-size:13px">
        <b>AI Payment Reminder Call - Ria (Edesy Vani)</b><br/><br/>
        <table style="border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Call ID</b></td><td>{call_sid}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Customer</b></td><td>{customer_name}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Outstanding</b></td><td>{outstanding_amount}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Oldest Due</b></td><td>{oldest_due_date}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Salesman</b></td><td>{salesman_name}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Duration</b></td><td>{mins}m {secs}s</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Outcome</b></td><td>{outcome_label}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:#666"><b>Summary</b></td><td>{summary}</td></tr>
        </table>
        <br/>{rec_link}
        <hr style="border:none;border-top:1px solid #ddd;margin:8px 0"/>
        <small style="color:#666"><b>Transcript:</b><br/>
        <pre style="white-space:pre-wrap;font-size:12px">{transcript_snippet}</pre>
        </small></div>
    """

    try:
        uid, models = get_odoo_connection()
        models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'res.partner', 'message_post',
            [partner_id],
            {'body': body, 'message_type': 'comment', 'subtype_xmlid': 'mail.mt_note'}
        )
        _logger.info(f'Logged call {call_sid} on partner {partner_id} ({customer_name})')
        return jsonify({'status': 'ok', 'partner_id': partner_id}), 200
    except Exception as e:
        _logger.error(f'Odoo write error: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
