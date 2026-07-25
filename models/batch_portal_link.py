from odoo import api, fields, models


class StudentBatchFeePortalLink(models.Model):
    _inherit = 'student.batch'

    fee_portal_url = fields.Char(
        string='Fee Portal Link', compute='_compute_fee_portal_url',
        help="Shareable public link showing this batch's fees, with a "
             "'Pay this' button straight into Quick Pay for each fee type.",
    )

    @api.depends()
    def _compute_fee_portal_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.fee_portal_url = f"{base_url}/quick-pay/batch/{rec.id}" if rec.id else False
