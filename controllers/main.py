import base64
import logging

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

FEE_TYPES = [
    ('reservation', 'Batch Reservation Fee'),
    ('admission', 'Admission Fee'),
    ('full_course', 'Full Course Fee'),
]


class QuickPayPortal(http.Controller):

    @http.route('/quick-pay', type='http', auth='public', website=True, sitemap=True)
    def quick_pay_form(self, batch_id=None, fee_type=None, phone=None, **kw):
        batches = request.env['student.batch'].sudo().search([('active', '=', True)])
        form_values = {}
        if batch_id:
            form_values['batch_id'] = batch_id
        if fee_type in dict(FEE_TYPES):
            form_values['fee_type'] = fee_type
        if phone:
            form_values['phone'] = phone
        return request.render('quick_pay.portal_quick_pay_form', {
            'batches': batches,
            'fee_types': FEE_TYPES,
            'error': None,
            'form_values': form_values,
        })

    @http.route('/quick-pay/check_status', type='json', auth='public', website=True)
    def quick_pay_check_status(self, batch_id=None, phone=None, **kw):
        """Used by the main payment form once batch+phone are both known —
        tells the UI whether to restrict Fee Type to just the balance
        (has_balance) or show the completion message (fully_paid)."""
        status = request.env['quick.pay'].sudo().check_batch_payment_status(batch_id, phone)
        if status['status'] != 'new':
            status['company_name'] = request.env.company.name
        return status

    @http.route('/quick-pay/fee_amount', type='json', auth='public', website=True)
    def quick_pay_fee_amount(self, batch_id=None, fee_type=None, fee_structure_id=None,
                              phone=None, **kw):
        """Server-side amount lookup used to update the displayed amount
        as the visitor picks a batch/fee type/phone — informational only,
        the real amount is always recomputed server-side again on submit."""
        if not batch_id or not fee_type:
            return {'amount': 0.0}
        record = request.env['quick.pay'].sudo().new({
            'batch_id': int(batch_id),
            'fee_type': fee_type,
            'fee_structure_id': int(fee_structure_id) if fee_structure_id else False,
            'phone': phone or False,
        })
        breakdown = record._resolve_fee_breakdown()
        plans = []
        if fee_type == 'full_course' and not breakdown['is_balance']:
            plans = [
                {'id': fs.id, 'name': fs.name}
                for fs in record.available_fee_structure_ids
            ]
        return {
            'amount': breakdown['inclusive'],
            'is_balance': breakdown['is_balance'],
            'plans': plans,
        }

    @http.route('/quick-pay/submit', type='http', auth='public', website=True,
                methods=['POST'], csrf=False)
    def quick_pay_submit(self, **post):
        Batch = request.env['student.batch'].sudo()
        batches = Batch.search([('active', '=', True)])

        def render_error(message):
            return request.render('quick_pay.portal_quick_pay_form', {
                'batches': batches,
                'fee_types': FEE_TYPES,
                'error': message,
                'form_values': post,
            })

        student_name = (post.get('student_name') or '').strip()
        phone = (post.get('phone') or '').strip()
        batch_id = post.get('batch_id')
        fee_type = post.get('fee_type')

        if not student_name:
            return render_error(_('Student Name is required.'))
        if not phone:
            return render_error(_('Registered Mobile Number is required.'))
        if not batch_id:
            return render_error(_('Please select a batch.'))
        if not fee_type or fee_type not in dict(FEE_TYPES):
            return render_error(_('Please select a fee type.'))

        upload = request.httprequest.files.get('payment_slip')
        if not upload or not upload.filename:
            return render_error(_('Please upload your payment slip.'))

        vals = {
            'student_name': student_name,
            'phone': phone,
            'place': post.get('place') or False,
            'email': post.get('email') or False,
            'batch_id': int(batch_id),
            'fee_type': fee_type,
            'transaction_number': post.get('transaction_number') or False,
            'remarks': post.get('remarks') or False,
            'payment_slip': base64.b64encode(upload.read()),
            'payment_slip_filename': upload.filename,
        }
        if fee_type == 'full_course' and post.get('fee_structure_id'):
            vals['fee_structure_id'] = int(post['fee_structure_id'])

        try:
            record = request.env['quick.pay'].sudo().create(vals)
        except ValidationError as exc:
            return render_error(exc.args[0] if exc.args else str(exc))
        except Exception:
            _logger.exception("Quick Pay submission failed")
            return render_error(_(
                'Something went wrong submitting your payment request. '
                'Please try again or contact us.'
            ))

        return request.redirect(f"/quick-pay/thank-you?ref={record.name}")

    @http.route('/quick-pay/thank-you', type='http', auth='public', website=True)
    def quick_pay_thank_you(self, ref=None, **kw):
        return request.render('quick_pay.portal_quick_pay_thank_you', {'ref': ref})

    @http.route('/quick-pay/batch/<int:batch_id>', type='http', auth='public',
                website=True, sitemap=True)
    def quick_pay_batch_info(self, batch_id, **kw):
        """Shareable per-batch fee info page — shows every fee configured
        for this batch (Reservation / Admission / Full Course) with a
        direct 'Pay This' link into the Quick Pay form, pre-selected for
        this batch and fee type."""
        batch = request.env['student.batch'].sudo().browse(batch_id)
        if not batch.exists() or not batch.active:
            return request.render('quick_pay.portal_batch_not_found', {})

        fee_cards = []
        for code, label in FEE_TYPES:
            record = request.env['quick.pay'].sudo().new({
                'batch_id': batch.id, 'fee_type': code,
            })
            breakdown = record._resolve_fee_breakdown()
            if breakdown['inclusive'] > 0:
                fee_cards.append({
                    'code': code, 'label': label,
                    'amount': breakdown['inclusive'],
                })

        # "Pay the full amount" — course fee + admission fee added
        # together (see quick.pay._resolve_fee_breakdown).
        full_course = next((c for c in fee_cards if c['code'] == 'full_course'), None)

        return request.render('quick_pay.portal_batch_fee_info', {
            'batch': batch,
            'fee_cards': fee_cards,
            'full_course': full_course,
        })

    @http.route('/quick-pay/batch_fee_amounts', type='json', auth='public', website=True)
    def quick_pay_batch_fee_amounts(self, batch_id=None, phone=None, **kw):
        """Used by the batch fee-info page's 'Check My Balance' box.
        Returns one of three states: 'new' (show all fee options),
        'has_balance' (show only the remaining balance), or 'fully_paid'
        (show a completion message instead of any payment option)."""
        if not batch_id:
            return {'status': 'new', 'fees': []}

        status = request.env['quick.pay'].sudo().check_batch_payment_status(batch_id, phone)
        if status['status'] != 'new':
            status['company_name'] = request.env.company.name
            return status

        fees = []
        for code, label in FEE_TYPES:
            record = request.env['quick.pay'].sudo().new({
                'batch_id': int(batch_id), 'fee_type': code, 'phone': phone or False,
            })
            breakdown = record._resolve_fee_breakdown()
            if breakdown['inclusive'] > 0:
                fees.append({
                    'code': code, 'label': label,
                    'amount': breakdown['inclusive'],
                    'is_balance': breakdown['is_balance'],
                })
        return {'status': 'new', 'fees': fees}

    @http.route('/quick-pay/search', type='http', auth='public', website=True, sitemap=True)
    def quick_pay_search(self, ref=None, phone=None, **kw):
        """Students look up their own payment history and any
        outstanding balance using either their Payment Reference Number
        or their registered mobile number — no login required, matching
        the rest of Quick Pay's public flow."""
        ref = (ref or '').strip()
        phone = (phone or '').strip()
        results = None
        balances = []
        error = None

        if ref or phone:
            Payment = request.env['quick.pay'].sudo()
            if ref:
                results = Payment.search([('name', '=', ref)], limit=1)
            else:
                results = Payment.search(
                    [('phone', '=', phone)], order='submission_date desc', limit=50)

            if not results:
                error = _("No payment records found for that reference number or mobile number.")
            else:
                lookup_phone = phone or results[:1].phone
                lead = request.env['leads.logic'].sudo().search(
                    [('phone_number', '=', lookup_phone)], limit=1)
                if lead and lead.student_id:
                    for enr in lead.student_id.enrollment_ids:
                        if enr.due_amount > 0:
                            balances.append({
                                'batch_id': enr.batch_id.id,
                                'batch_name': enr.batch_id.name,
                                'due_amount': enr.due_amount,
                                'phone': lookup_phone,
                            })

        state_labels = dict(request.env['quick.pay']._fields['state'].selection)
        fee_type_labels = dict(FEE_TYPES)
        rows = [{
            'name': r.name,
            'date': r.submission_date.strftime('%d-%b-%Y') if r.submission_date else '',
            'batch_name': r.batch_id.name,
            'fee_type_label': fee_type_labels.get(r.fee_type, r.fee_type),
            'amount': r.fee_amount,
            'state': r.state,
            'state_label': state_labels.get(r.state, r.state),
        } for r in (results or [])]

        return request.render('quick_pay.portal_search', {
            'ref': ref, 'phone': phone,
            'rows': rows, 'balances': balances, 'error': error,
        })

    @http.route('/quick-pay/receipt/<string:ref>', type='http', auth='public', website=True)
    def quick_pay_receipt_download(self, ref, **kw):
        """Public receipt download — gated by knowing the reference
        number itself (the same identifier used throughout the search
        flow), not a raw sequential database id."""
        record = request.env['quick.pay'].sudo().search([('name', '=', ref)], limit=1)
        if not record or record.state != 'converted':
            return request.not_found()
        pdf_content, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'quick_pay.action_report_quick_pay_receipt', record.ids)
        headers = [
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', f'inline; filename="Receipt-{record.name}.pdf"'),
        ]
        return request.make_response(pdf_content, headers=headers)

