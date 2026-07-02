"""
trigger.py - Middleware to pull overdue data from Odoo and trigger Edesy Vani calls
Run daily (e.g. 9 AM) via cron or scheduler.

Logic:
- outstanding_amount = total of all unpaid/partially-paid invoices (full picture)
- pdc_details = human-readable summary of pending PDC cheques (not subtracted,
  just disclosed if customer raises it during the call)
"""

import xmlrpc.client
import requests
from datetime import date
from collections import defaultdict

# ── Odoo config ────────────────────────────────────────────────────
ODOO_URL     = 'https://brutgroup.in'
ODOO_DB      = 'odoo_db'
ODOO_USER    = 'idealdestributors2025@gmail.com'
ODOO_API_KEY = '78e2ba04421d13fa5d3839ffad7274bc42149804'

# ── Edesy config ───────────────────────────────────────────────────
EDESY_API_KEY    = 'vp_live_a3b677779de33a7b00577232deecbaf725cf46b0602cd08b4cd02bd8f6e86be6'
EDESY_AGENT_ID   = 23810
EDESY_WORKSPACE  = 'cmr0w80sz1766pe1o1jjvg7de'
WEBHOOK_URL      = 'https://edesy-odoo-webhook.onrender.com/edesy/webhook'

# Company name map (Odoo company_id -> display name for the call)
COMPANY_NAMES = {
    2: 'Ideal Distributors',
    3: 'Brut Enterprises',
    4: 'Brut Agencies',
    5: 'Brut Enterprises Kottayam',
}

def odoo_connect():
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    uid    = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
    models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
    return uid, models

def fetch_overdue_invoices(uid, models):
    """Fetch all overdue unpaid/partial invoices."""
    return models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY,
        'account.move', 'search_read',
        [[
            ['move_type',        '=',  'out_invoice'],
            ['state',            '=',  'posted'],
            ['payment_state',    'in', ['not_paid', 'partial']],
            ['invoice_date_due', '<',  str(date.today())],
        ]],
        {'fields': ['partner_id', 'name', 'amount_residual',
                    'invoice_date_due', 'invoice_user_id', 'company_id'],
         'limit': 500}
    )

def fetch_pdc_for_partner(uid, models, partner_id):
    """
    Fetch pending PDC cheques for a partner.
    State 'deposit' = deposited but not cleared.
    Returns (count, total_amount, detail_string)
    """
    cheques = models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY,
        'dev.deposit.cheque', 'search_read',
        [[
            ['partner_id', '=', partner_id],
            ['state',      '=', 'deposit'],   # deposited, not yet cleared
        ]],
        {'fields': ['name', 'amount', 'cheque_date'], 'limit': 50}
    )
    if not cheques:
        return 0, 0.0, 'no pending PDC cheques'

    count  = len(cheques)
    total  = sum(c['amount'] for c in cheques)
    dates  = sorted(c['cheque_date'] for c in cheques)
    detail = (f"{count} cheque{'s' if count > 1 else ''} "
              f"totalling \u20b9{total:,.0f} "
              f"(dates: {', '.join(dates)})")
    return count, total, detail

def get_partner_phone(uid, models, partner_id):
    p = models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY,
        'res.partner', 'read', [partner_id],
        {'fields': ['name', 'phone', 'company_id']}
    )[0]
    return p

def normalise_phone(phone):
    if not phone:
        return None
    clean = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    if not clean.startswith('+'):
        clean = '+91' + clean.lstrip('0')
    return clean

def trigger_call(phone, partner_id, company_name, customer_name,
                 outstanding_amount, invoice_count, oldest_due_date,
                 salesman_name, pdc_details):
    resp = requests.post(
        'https://voice-agent.edesy.in/api/initiate-call',
        headers={
            'Content-Type':  'application/json',
            'Authorization': f'Bearer {EDESY_API_KEY}'
        },
        json={
            'phone_number':  phone,
            'agent_id':      EDESY_AGENT_ID,
            'workspace_id':  EDESY_WORKSPACE,
            'provider':      'edesy-ivr',
            'callbackUrl':   WEBHOOK_URL,
            'variables': {
                'customer_name':      customer_name,
                'company_name':       company_name,
                'outstanding_amount': f'\u20b9{outstanding_amount:,.0f}',
                'invoice_count':      str(invoice_count),
                'oldest_due_date':    oldest_due_date,
                'salesman_name':      salesman_name,
                'pdc_details':        pdc_details,
                'odoo_partner_id':    str(partner_id),
            }
        },
        timeout=30
    )
    return resp.json()

def run():
    print(f"Starting payment reminder calls — {date.today()}")
    uid, models = odoo_connect()

    invoices = fetch_overdue_invoices(uid, models)
    print(f"Found {len(invoices)} overdue invoices")

    # Group by partner
    by_partner = defaultdict(list)
    for inv in invoices:
        by_partner[inv['partner_id'][0]].append(inv)

    called, skipped = 0, 0

    for partner_id, pinvoices in by_partner.items():
        # Aggregate invoice data
        total_outstanding = sum(i['amount_residual'] for i in pinvoices)
        oldest_due        = min(i['invoice_date_due'] for i in pinvoices)
        inv_count         = len(pinvoices)
        salesman          = (pinvoices[0]['invoice_user_id'][1]
                             if pinvoices[0]['invoice_user_id']
                             else 'Accounts Team')
        company_id        = (pinvoices[0]['company_id'][0]
                             if pinvoices[0]['company_id'] else 2)
        company_name      = COMPANY_NAMES.get(company_id, 'Ideal Distributors')

        # Get PDC cheque summary for this partner
        pdc_count, pdc_total, pdc_details = fetch_pdc_for_partner(
            uid, models, partner_id
        )

        # Get partner phone
        partner = get_partner_phone(uid, models, partner_id)
        phone   = normalise_phone(partner.get('phone'))

        if not phone:
            print(f"  SKIP {partner['name']} — no phone number")
            skipped += 1
            continue

        print(f"  Calling {partner['name']} ({phone})")
        print(f"    Outstanding: \u20b9{total_outstanding:,.0f} | "
              f"PDC: {pdc_details}")

        result = trigger_call(
            phone        = phone,
            partner_id   = partner_id,
            company_name = company_name,
            customer_name= partner['name'],
            outstanding_amount = total_outstanding,
            invoice_count      = inv_count,
            oldest_due_date    = oldest_due,
            salesman_name      = salesman,
            pdc_details        = pdc_details,
        )

        if result.get('success') or result.get('status') == 'ok':
            print(f"    ✓ Call triggered")
            called += 1
        else:
            print(f"    ✗ Failed: {result}")
            skipped += 1

    print(f"Done — {called} calls triggered, {skipped} skipped")

if __name__ == '__main__':
    run()
