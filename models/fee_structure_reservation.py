from odoo import fields, models


class FeeStructureReservation(models.Model):
    """Adds a 'Batch Reservation Fee' fee type, configured per-batch the
    same way Admission Fee already is, so Quick Pay never hardcodes an
    amount — it's always read from fee.structure like every other fee."""
    _inherit = 'fee.structure'

    fee_type = fields.Selection(
        selection_add=[('reservation', 'Batch Reservation Fee')],
        ondelete={'reservation': 'set default'},
    )
