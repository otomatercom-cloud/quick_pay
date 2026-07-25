from odoo import _, fields, models
from odoo.exceptions import ValidationError


class QuickPayRejectWizard(models.TransientModel):
    _name = 'quick.pay.reject.wizard'
    _description = 'Reject Quick Pay Request'

    quick_pay_id = fields.Many2one('quick.pay', required=True, readonly=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise ValidationError(_("Please enter a reason for rejection."))
        record = self.quick_pay_id
        record.write({
            'state': 'rejected',
            'reject_reason': self.reason,
            'verified_date': fields.Datetime.now(),
            'verified_by': self.env.user.id,
        })
        record.message_post(body=_("Payment request rejected. Reason: %s") % self.reason)
        return {'type': 'ir.actions.act_window_close'}
