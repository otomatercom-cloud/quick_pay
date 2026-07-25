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
    def quick_pay_form(self, **kw):
        batches = request.env['student.batch'].sudo().search([('active', '=', True)])
        return request.render('quick_pay.portal_quick_pay_form', {
            'batches': batches,
            'fee_types': FEE_TYPES,
            'error': None,
            'form_values': {},
        })

    @http.route('/quick-pay/fee_amount', type='json', auth='public', website=True)
    def quick_pay_fee_amount(self, batch_id=None, fee_type=None, fee_structure_id=None, **kw):
        """Server-side amount lookup used to update the displayed amount
        as the visitor picks a batch/fee type — informational only, the
        real amount is always recomputed server-side again on submit."""
        if not batch_id or not fee_type:
            return {'amount': 0.0}
        record = request.env['quick.pay'].sudo().new({
            'batch_id': int(batch_id),
            'fee_type': fee_type,
            'fee_structure_id': int(fee_structure_id) if fee_structure_id else False,
        })
        plans = []
        if fee_type == 'full_course':
            plans = [
                {'id': fs.id, 'name': fs.name}
                for fs in record.available_fee_structure_ids
            ]
        return {'amount': record._resolve_fee_amount(), 'plans': plans}

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

