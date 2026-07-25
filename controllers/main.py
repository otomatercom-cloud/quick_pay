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
    def quick_pay_form(self, batch_id=None, fee_type=None, **kw):
        batches = request.env['student.batch'].sudo().search([('active', '=', True)])
        form_values = {}
        if batch_id:
            form_values['batch_id'] = batch_id
        if fee_type in dict(FEE_TYPES):
            form_values['fee_type'] = fee_type
        return request.render('quick_pay.portal_quick_pay_form', {
            'batches': batches,
            'fee_types': FEE_TYPES,
            'error': None,
            'form_values': form_values,
        })

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
                methods=['POST'], csrf=True)
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

        # "Pay the full amount" — the course's own total, which already
        # includes the admission fee as a first instalment towards it
        # (not stacked on top of it) — same math the payment form uses.
        full_course = next((c for c in fee_cards if c['code'] == 'full_course'), None)

        return request.render('quick_pay.portal_batch_fee_info', {
            'batch': batch,
            'fee_cards': fee_cards,
            'full_course': full_course,
        })

