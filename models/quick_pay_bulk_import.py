import base64
import io
import logging
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

REQUIRED_HEADERS = {'name', 'number', 'batch', 'paid'}


class QuickPayBulkImportWizard(models.TransientModel):
    """Backfills historical/already-collected payments (e.g. cash or
    bank-transfer payments taken before Quick Pay existed, or offline)
    so the system's outstanding-balance figures are accurate going
    forward. This does NOT create quick.pay portal-submission records —
    those represent genuine self-service form submissions. Instead it
    goes straight to student.details + student.enrollment +
    student.fee.payment, reusing quick.pay's own tested lead/student/
    enrollment logic via an in-memory (.new()) record rather than
    duplicating it."""
    _name = 'quick.pay.bulk.import.wizard'
    _description = 'Bulk Import Historical Payments'

    import_file = fields.Binary(string='Spreadsheet (.xlsx)', required=True)
    import_filename = fields.Char()
    result_summary = fields.Text(readonly=True)

    def action_import(self):
        self.ensure_one()
        if not self.import_file:
            raise UserError(_("Upload a file first."))

        try:
            import openpyxl
        except ImportError as exc:
            raise UserError(_("openpyxl is not available on this server.")) from exc

        wb = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(self.import_file)), data_only=True)
        sheet = wb.active

        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise UserError(_("The file appears to be empty."))

        header_row = [str(h).strip().lower() if h else '' for h in rows[0]]
        missing = REQUIRED_HEADERS - set(header_row)
        if missing:
            raise UserError(_(
                "Missing required column(s): %s. Expected headers: "
                "Name, Number, Email, Batch, Paid."
            ) % ', '.join(sorted(missing)))
        col = {h: i for i, h in enumerate(header_row)}

        Batch = self.env['student.batch']
        imported = 0
        skipped = []

        for row_num, row in enumerate(rows[1:], start=2):
            name = self._cell(row, col.get('name'))
            phone_raw = self._cell(row, col.get('number'))
            email = self._cell(row, col.get('email'))
            batch_name = self._cell(row, col.get('batch'))
            paid_raw = self._cell(row, col.get('paid'))

            if not name and not phone_raw:
                continue  # blank trailing row

            phone = re.sub(r'\D', '', str(phone_raw or ''))
            if not phone:
                skipped.append(_("Row %d: no valid phone number.") % row_num)
                continue

            try:
                paid_amount = float(paid_raw)
            except (TypeError, ValueError):
                skipped.append(_("Row %(r)d (%(n)s): 'Paid' amount isn't a number.") % {
                    'r': row_num, 'n': name or phone})
                continue
            if paid_amount <= 0:
                skipped.append(_("Row %(r)d (%(n)s): 'Paid' amount must be greater than 0.") % {
                    'r': row_num, 'n': name or phone})
                continue

            batch = Batch.search([('name', '=ilike', (batch_name or '').strip())], limit=1)
            if not batch:
                skipped.append(_("Row %(r)d (%(n)s): batch '%(b)s' not found.") % {
                    'r': row_num, 'n': name or phone, 'b': batch_name})
                continue

            try:
                qp = self.env['quick.pay'].new({
                    'student_name': name or phone,
                    'phone': phone,
                    'email': email or False,
                    'batch_id': batch.id,
                    'fee_type': 'full_course',
                })
                lead = qp._find_or_create_lead()
                course_fs = qp._resolve_admission_fee_structure()
                if not course_fs:
                    skipped.append(_(
                        "Row %(r)d (%(n)s): no course fee structure configured "
                        "for batch '%(b)s'."
                    ) % {'r': row_num, 'n': name or phone, 'b': batch.name})
                    continue

                student, enrollment = self.env['quick.pay']._ensure_student_and_enrollment(
                    lead, batch, course_fs, source_label=_('Bulk Payment Import'))

                self.env['student.fee.payment'].create({
                    'enrollment_id': enrollment.id,
                    'amount': paid_amount,
                    'payment_mode': 'other',
                    'receipt_no': 'BULK-IMPORT',
                    'remarks': _('Bulk historical payment import (row %d).') % row_num,
                })
                imported += 1
            except Exception as exc:  # noqa: BLE001
                _logger.warning("Bulk payment import failed on row %d: %s", row_num, exc)
                skipped.append(_("Row %(r)d (%(n)s): %(err)s") % {
                    'r': row_num, 'n': name or phone, 'err': str(exc)})

        summary_lines = [_("%d payment(s) imported successfully.") % imported]
        if skipped:
            summary_lines.append('')
            summary_lines.append(_("%d row(s) skipped:") % len(skipped))
            summary_lines.extend(skipped)
        self.result_summary = '\n'.join(summary_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'quick.pay.bulk.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @staticmethod
    def _cell(row, index):
        if index is None or index >= len(row):
            return None
        value = row[index]
        return str(value).strip() if isinstance(value, str) else value
