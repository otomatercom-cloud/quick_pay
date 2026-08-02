import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

FEE_TYPE_SELECTION = [
    ('reservation', 'Batch Reservation Fee'),
    ('admission', 'Admission Fee'),
    ('full_course', 'Full Course Fee'),
]

STATE_SELECTION = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('verified', 'Verified'),
    ('rejected', 'Rejected'),
    ('converted', 'Converted'),
    ('cancelled', 'Cancelled'),
]

QUICK_PAY_SOURCE_XMLID = 'quick_pay.leads_source_quick_pay'


class QuickPay(models.Model):
    _name = 'quick.pay'
    _description = 'Quick Pay - Pre-Admission Payment Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char(string='Reference', default='New', readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)

    student_name = fields.Char(required=True, tracking=True)
    phone = fields.Char(string='Registered Mobile Number', required=True, tracking=True)
    place = fields.Char()
    email = fields.Char()
    admission_officer_id = fields.Many2one(
        'hr.employee', string='Admission Officer',
        domain=lambda self: [
            ('id', 'in', self.env['lead.team.member'].sudo().search([]).mapped('employee_id').ids)
        ],
        help="Which Admission Officer helped this student — set as the "
             "Lead Owner if a new lead is created from this submission.")

    batch_id = fields.Many2one(
        'student.batch', string='Batch', required=True,
        domain=[('active', '=', True)], tracking=True,
    )
    fee_type = fields.Selection(FEE_TYPE_SELECTION, required=True, tracking=True)

    available_fee_structure_ids = fields.Many2many(
        'fee.structure', compute='_compute_available_fee_structures',
        string='Available Course Fee Plans',
    )
    fee_structure_id = fields.Many2one(
        'fee.structure', string='Course Fee Plan',
        domain="[('id', 'in', available_fee_structure_ids)]",
        help="Only relevant for Full Course Fee, if the batch has more "
             "than one plan (e.g. lump sum vs installment).",
    )
    fee_amount = fields.Float(
        string='Fee Amount ₹', digits=(10, 2),
        compute='_compute_fee_amount', store=True, readonly=True,
    )
    base_amount = fields.Float(
        string='Base Amount (Excl. GST) ₹', digits=(10, 2),
        compute='_compute_fee_amount', store=True, readonly=True,
    )
    gst_amount = fields.Float(
        string='GST ₹', digits=(10, 2),
        compute='_compute_fee_amount', store=True, readonly=True,
    )
    gst_rate = fields.Char(
        string='GST Rate', compute='_compute_fee_amount', store=True, readonly=True,
    )
    is_balance_amount = fields.Boolean(
        string='Is Outstanding Balance', compute='_compute_fee_amount', store=True,
        help="True when the Full Course Fee amount shown is the student's "
             "actual remaining balance on an existing enrollment, rather "
             "than the full course price.",
    )

    payment_slip = fields.Binary(string='Payment Slip', attachment=True)
    payment_slip_filename = fields.Char()
    transaction_number = fields.Char(string='Transaction Number')
    remarks = fields.Text()

    submission_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    verified_date = fields.Datetime(readonly=True, copy=False)
    verified_by = fields.Many2one('res.users', readonly=True, copy=False)
    reject_reason = fields.Text(readonly=True, copy=False)

    state = fields.Selection(
        STATE_SELECTION, default='draft', required=True,
        tracking=True, copy=False,
    )

    lead_id = fields.Many2one('leads.logic', readonly=True, copy=False)
    student_id = fields.Many2one('student.details', readonly=True, copy=False)
    enrollment_id = fields.Many2one('student.enrollment', readonly=True, copy=False)
    payment_id = fields.Many2one('student.fee.payment', readonly=True, copy=False)

    # ── existing lead/student match preview (backend only — never exposed
    # on the public portal, since showing "this number belongs to X" to an
    # anonymous visitor would let anyone probe phone numbers) ────────────
    existing_lead_id = fields.Many2one(
        'leads.logic', compute='_compute_existing_match',
        string='Matching Lead',
    )
    existing_student_id = fields.Many2one(
        'student.details', compute='_compute_existing_match',
        string='Matching Student',
    )
    existing_match_note = fields.Char(compute='_compute_existing_match')

    @api.depends('phone')
    def _compute_existing_match(self):
        Lead = self.env['leads.logic'].sudo()
        for rec in self:
            lead = Lead.search([('phone_number', '=', rec.phone)], limit=1) if rec.phone else Lead
            rec.existing_lead_id = lead.id if lead else False
            rec.existing_student_id = lead.student_id.id if lead and lead.student_id else False
            if lead and lead.student_id:
                rec.existing_match_note = _(
                    "This mobile number matches an existing student: %(name)s (Reg. No: %(reg)s)."
                ) % {'name': lead.student_id.name, 'reg': lead.student_id.registration_no}
            elif lead:
                rec.existing_match_note = _(
                    "This mobile number matches an existing lead: %s (not yet admitted)."
                ) % lead.name
            else:
                rec.existing_match_note = False

    # ── computed fee resolution (never hardcoded) ───────────────────────
    @api.depends('batch_id')
    def _compute_available_fee_structures(self):
        for rec in self:
            rec.available_fee_structure_ids = (
                rec.batch_id.fee_structure_ids.filtered(
                    lambda f: f.fee_type in ('lumpsum', 'installment'))
                if rec.batch_id else self.env['fee.structure']
            )

    def _find_existing_enrollment(self):
        """The student's existing enrollment in the selected batch, if any
        — used so 'Full Course Fee' shows the real outstanding balance
        (e.g. after a reservation/admission fee or a previous instalment)
        rather than the full sticker price every time."""
        self.ensure_one()
        if not self.phone or not self.batch_id:
            return self.env['student.enrollment']
        lead = self.env['leads.logic'].sudo().search(
            [('phone_number', '=', self.phone)], limit=1)
        if not lead or not lead.student_id:
            return self.env['student.enrollment']
        return lead.student_id.enrollment_ids.filtered(
            lambda e: e.batch_id == self.batch_id)[:1]

    @api.model
    def check_batch_payment_status(self, batch_id, phone):
        """Used by both the batch fee-info page and the payment form once
        a phone number is entered — tells the UI whether to show all fee
        options (new), only the remaining balance (has_balance, hiding
        Admission/Reservation since they're already covered by whatever
        was paid before), or a completion message (fully_paid)."""
        phone = (phone or '').strip()
        if not phone or not batch_id:
            return {'status': 'new'}
        record = self.new({'batch_id': int(batch_id), 'phone': phone})
        enrollment = record._find_existing_enrollment()
        if not enrollment:
            return {'status': 'new'}
        if enrollment.due_amount <= 0:
            return {'status': 'fully_paid'}
        return {'status': 'has_balance', 'balance': enrollment.due_amount}

    def _resolve_fee_breakdown(self):
        """Returns {'inclusive', 'exclusive', 'gst', 'gst_rate'} resolved
        from the batch's fee.structure — never hardcoded, and computed the
        same way for portal preview, the stored fields, and reporting."""
        self.ensure_one()
        zero = {'inclusive': 0.0, 'exclusive': 0.0, 'gst': 0.0, 'gst_rate': '0', 'is_balance': False}
        if not self.batch_id or not self.fee_type:
            return zero
        if self.fee_type in ('reservation', 'admission'):
            fs = self.batch_id.fee_structure_ids.filtered(
                lambda f: f.fee_type == self.fee_type)[:1]
            if not fs:
                return zero
            incl = sum(fs.mapped('amount_inclusive'))
            excl = sum(fs.mapped('amount_exclusive'))
            return {
                'inclusive': incl, 'exclusive': excl,
                'gst': round(incl - excl, 2), 'gst_rate': fs[:1].gst_rate,
                'is_balance': False,
            }
        # full_course — prefer the real outstanding balance if the student
        # already has an enrollment in this batch (part-paid or freshly
        # admitted with dues still open); otherwise fall back to the full
        # course fee for a brand-new admission.
        enrollment = self._find_existing_enrollment()
        if enrollment:
            fs = enrollment.fee_structure_id
            rate = float((fs.gst_rate if fs else enrollment.gst_rate) or 0)
            incl = enrollment.due_amount
            excl = round(incl / (1 + rate / 100), 2) if rate else incl
            return {
                'inclusive': incl, 'exclusive': excl,
                'gst': round(incl - excl, 2), 'gst_rate': fs.gst_rate if fs else '0',
                'is_balance': True,
            }
        fs = self.fee_structure_id or self.available_fee_structure_ids[:1]
        if not fs:
            return zero
        if fs.fee_type == 'installment':
            incl = fs.total_fee_amount
            rate = float(fs.gst_rate or 0)
            excl = round(incl / (1 + rate / 100), 2) if rate else incl
        else:
            incl = fs.amount_inclusive
            excl = fs.amount_exclusive
        # Admission Fee is a separate charge added on top of the course
        # fee (not absorbed into it) — e.g. lumpsum ₹3500 + admission
        # ₹500 = ₹4000 "Full Course Fee".
        admission_fs = self.batch_id.fee_structure_ids.filtered(
            lambda f: f.fee_type == 'admission')
        incl += sum(admission_fs.mapped('amount_inclusive'))
        excl += sum(admission_fs.mapped('amount_exclusive'))
        return {
            'inclusive': incl, 'exclusive': excl,
            'gst': round(incl - excl, 2), 'gst_rate': fs.gst_rate,
            'is_balance': False,
        }

    def _resolve_fee_amount(self):
        self.ensure_one()
        return self._resolve_fee_breakdown()['inclusive']

    @api.depends('batch_id', 'fee_type', 'fee_structure_id', 'phone')
    def _compute_fee_amount(self):
        for rec in self:
            if rec.state not in ('draft', 'submitted'):
                # Once verified/rejected/cancelled, this amount is history
                # — e.g. after a Full Course Fee payment settles the
                # balance, the enrollment's due_amount drops to 0, and
                # blindly recomputing here (which happens automatically
                # any time a dependency like 'phone' is touched, including
                # on module upgrade) would overwrite the amount actually
                # paid with 0. Leave the stored value exactly as it was
                # at the time it was locked in.
                continue
            breakdown = rec._resolve_fee_breakdown()
            rec.fee_amount = breakdown['inclusive']
            rec.base_amount = breakdown['exclusive']
            rec.gst_amount = breakdown['gst']
            rec.gst_rate = breakdown['gst_rate']
            rec.is_balance_amount = breakdown['is_balance']

    @api.onchange('batch_id', 'fee_type', 'fee_structure_id')
    def _onchange_fee_inputs(self):
        self.fee_structure_id = False if self.fee_type != 'full_course' else self.fee_structure_id
        self.fee_amount = self._resolve_fee_amount()

    # ── validations ──────────────────────────────────────────────────────
    @api.constrains('fee_amount', 'state')
    def _check_fee_amount(self):
        for rec in self:
            # Only enforce this at the point of submission — once
            # verified/rejected/cancelled, fee_amount is a locked
            # historical value (see _compute_fee_amount) and must never
            # be re-validated against the batch's *current* fee setup.
            if rec.state == 'submitted' and rec.fee_amount <= 0:
                raise ValidationError(_(
                    "No fee amount is configured for '%s' on batch %s. "
                    "Set it up in the batch's fee structures first."
                ) % (dict(FEE_TYPE_SELECTION).get(rec.fee_type), rec.batch_id.name))

    @api.constrains('phone', 'batch_id', 'fee_type', 'state')
    def _check_duplicate_pending(self):
        for rec in self:
            if rec.state != 'submitted' or not rec.phone or not rec.batch_id:
                continue
            dup = self.search([
                ('id', '!=', rec.id),
                ('phone', '=', rec.phone),
                ('batch_id', '=', rec.batch_id.id),
                ('fee_type', '=', rec.fee_type),
                ('state', '=', 'submitted'),
            ], limit=1)
            if dup:
                raise ValidationError(_(
                    "A pending payment request already exists for this "
                    "student, batch and fee type (%s)."
                ) % dup.name)

    # ── create ───────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('quick.pay') or 'New'
            vals.setdefault('state', 'submitted')
        records = super().create(vals_list)
        for rec in records:
            rec.message_post(body=_(
                "Payment request submitted: ₹%(amount)s for %(fee_type)s."
            ) % {
                'amount': f"{rec.fee_amount:,.2f}",
                'fee_type': dict(FEE_TYPE_SELECTION).get(rec.fee_type),
            })
        return records

    # ── verify / reject / cancel ────────────────────────────────────────
    def action_verify(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only submitted requests can be verified."))
            rec._do_verify()

    def action_open_reject_wizard(self):
        self.ensure_one()
        if self.state != 'submitted':
            raise UserError(_("Only submitted requests can be rejected."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject Payment Request'),
            'res_model': 'quick.pay.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_quick_pay_id': self.id},
        }

    def action_cancel(self):
        for rec in self:
            if rec.state == 'converted':
                raise UserError(_("A converted request cannot be cancelled."))
            rec.state = 'cancelled'
            rec.message_post(body=_("Payment request cancelled."))

    # ── the automated admission flow ────────────────────────────────────
    def _get_or_create_quick_pay_source(self):
        source = self.env.ref(QUICK_PAY_SOURCE_XMLID, raise_if_not_found=False)
        if source:
            return source
        return self.env['leads.sources'].search(
            [('name', '=', 'Quick Pay')], limit=1
        ) or self.env['leads.sources'].create({'name': 'Quick Pay'})

    def _find_or_create_lead(self):
        self.ensure_one()
        Lead = self.env['leads.logic']
        lead = Lead.search([('phone_number', '=', self.phone)], limit=1)
        if lead:
            return lead
        vals = {
            'leads_source': self._get_or_create_quick_pay_source().id,
            'name': self.student_name,
            'phone_number': self.phone,
            'email_address': self.email or False,
            'place': self.place or False,
            'batch_preference': self.batch_id.name,
            'state': 'new',
        }
        if self.admission_officer_id:
            vals['lead_owner'] = self.admission_officer_id.id
        return Lead.create(vals)

    def _resolve_admission_fee_structure(self):
        """The fee.structure used to actually create the enrollment — this
        is always the batch's main course plan, regardless of which fee
        type the student paid via Quick Pay (reservation/admission/full),
        since all three are just different entry points into the same
        admission."""
        self.ensure_one()
        if self.fee_type == 'full_course' and self.fee_structure_id:
            return self.fee_structure_id
        return self.batch_id.fee_structure_ids.filtered(
            lambda f: f.fee_type in ('lumpsum', 'installment'))[:1]

    @api.model
    def _ensure_student_and_enrollment(self, lead, batch, course_fs, source_label=''):
        """Finds or creates the student.details for this lead, and finds
        or creates their enrollment in this specific batch. Shared by
        _do_verify() (the normal portal payment flow) and the bulk
        historical-payment import wizard — both need exactly this same
        "make sure the student+enrollment exist" step, just from
        different entry points, so it lives here once rather than twice.
        Returns (student, enrollment)."""
        if lead.student_profile_created or lead.student_id:
            student = lead.student_id
        else:
            # Reuse custom_leads_19's own field-mapping logic
            # (LeadAdmissionWizard._prepare_student_vals) via an unsaved
            # wizard instance — done inline rather than calling
            # action_confirm_admission() directly because it branches on
            # an ir.config_parameter ('admission_batch_required') that
            # this flow must not depend on: both callers always have a
            # batch and a fee plan resolved already, so it always wants
            # full admission.
            wiz = self.env['lead.admission.wizard'].new({
                'lead_id': lead.id,
                'batch_id': batch.id,
                'fee_structure_id': course_fs.id,
            })
            student = self.env['student.details'].create(wiz._prepare_student_vals())

            lead.write({
                'student_id': student.id,
                'adm_id': student.id,
                'student_profile_created': True,
                'admission_status': True,
                'admission_date': fields.Datetime.now(),
                'lead_quality': 'admission',
                'state': 'qualified',
                'current_status': 'admission',
                'admission_batch': batch.name,
                'student_name': student.name,
            })
            lead.message_post(body=_(
                "Student admission completed via Quick Pay (%s)."
            ) % source_label)

        # Ensure an enrollment exists for THIS batch specifically — this
        # must not depend on whether the student record is brand-new.
        # A student who already existed (e.g. a prior lead, or already
        # enrolled in a different batch) still needs a fresh enrollment
        # created here the first time they pay towards *this* batch,
        # otherwise the payment has nothing to attach to and the
        # outstanding balance never appears anywhere.
        enrollment = student.enrollment_ids.filtered(lambda e: e.batch_id == batch)[:1]
        if not enrollment:
            # total_fee = course fee + admission fee ADDED TOGETHER — they
            # are two separate charges (registration fee + tuition), not
            # one wrapping the other. E.g. lumpsum ₹3500 + admission ₹500
            # = ₹4000 total; paying the ₹500 admission fee leaves a
            # ₹3500 balance toward that combined total.
            course_total = course_fs.total_fee_amount if course_fs.fee_type == 'installment' \
                else course_fs.amount_inclusive
            admission_fs = batch.fee_structure_ids.filtered(
                lambda f: f.fee_type == 'admission')
            grand_total = course_total + sum(admission_fs.mapped('amount_inclusive'))
            enrollment = self.env['student.enrollment'].create({
                'student_id': student.id,
                'batch_id': batch.id,
                'fee_structure_id': course_fs.id,
                'total_fee': grand_total,
                'fee_type': course_fs.fee_type,
                'gst_rate': course_fs.gst_rate,
            })
        return student, enrollment

    def _do_verify(self):
        self.ensure_one()
        lead = self._find_or_create_lead()
        course_fs = self._resolve_admission_fee_structure()
        if not course_fs:
            raise UserError(_(
                "No course fee structure (lump sum/installment) is "
                "configured for batch '%s'. Set one up before verifying."
            ) % self.batch_id.name)

        student, enrollment = self._ensure_student_and_enrollment(
            lead, self.batch_id, course_fs, source_label=self.name)

        payment = False
        if self.fee_amount > 0 and enrollment:
            payment = self.env['student.fee.payment'].create({
                'enrollment_id': enrollment.id,
                'amount': self.fee_amount,
                'payment_mode': 'upi',
                'receipt_no': self.transaction_number or self.name,
                'remarks': _("Quick Pay (%(type)s) - Ref %(ref)s") % {
                    'type': dict(FEE_TYPE_SELECTION).get(self.fee_type),
                    'ref': self.name,
                },
            })

        self.write({
            'state': 'converted',
            'lead_id': lead.id,
            'student_id': student.id,
            'enrollment_id': enrollment.id if enrollment else False,
            'payment_id': payment.id if payment else False,
            'verified_date': fields.Datetime.now(),
            'verified_by': self.env.user.id,
        })
        self.message_post(body=_(
            "Payment verified. Admission completed for %(name)s (Reg. No: %(reg)s)."
        ) % {'name': student.name, 'reg': student.registration_no})
        self._generate_receipt_attachment()

    def _generate_receipt_attachment(self):
        """Auto-generate the PDF receipt as soon as a payment is
        verified, attached to the record (visible in chatter/Files) so
        it exists without anyone having to remember to print it."""
        self.ensure_one()
        try:
            pdf_content, _fmt = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'quick_pay.action_report_quick_pay_receipt', self.ids)
        except Exception:
            _logger.exception("Could not auto-generate receipt for %s", self.name)
            return False
        attachment = self.env['ir.attachment'].sudo().create({
            'name': f"Receipt-{self.name}.pdf",
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'res_model': 'quick.pay',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })
        self.message_post(body=_("Payment receipt generated."), attachment_ids=[attachment.id])
        return attachment
