from datetime import datetime, timedelta

from odoo import api, models

FEE_TYPE_LABELS = {
    'reservation': 'Batch Reservation Fee',
    'admission': 'Admission Fee',
    'full_course': 'Full Course Fee',
}


class QuickPayReport(models.Model):
    _inherit = 'quick.pay'

    @api.model
    def _report_date_bounds(self, date_from, date_to):
        today = datetime.now().date()
        d_from = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else today
        d_to = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else today
        if d_from > d_to:
            d_from, d_to = d_to, d_from
        start = f"{d_from} 00:00:00"
        end = f"{d_to} 23:59:59"
        return d_from, d_to, start, end

    @api.model
    def get_fee_report(self, date_from=None, date_to=None):
        d_from, d_to, start, end = self._report_date_bounds(date_from, date_to)

        collected = self.sudo().search_read(
            [('state', '=', 'converted'),
             ('verified_date', '>=', start), ('verified_date', '<=', end)],
            ['batch_id', 'fee_type', 'fee_amount', 'base_amount', 'gst_amount', 'verified_date'],
            limit=None,
        )
        pending_in_range = self.sudo().search_read(
            [('state', '=', 'submitted'),
             ('submission_date', '>=', start), ('submission_date', '<=', end)],
            ['batch_id', 'fee_type', 'fee_amount'],
            limit=None,
        )
        pending_total = self.sudo().search_count([('state', '=', 'submitted')])
        rejected_count = self.sudo().search_count([
            ('state', '=', 'rejected'),
            ('verified_date', '>=', start), ('verified_date', '<=', end),
        ])

        summary = {
            'total_requests': len(collected),
            'total_collected': 0.0,
            'total_base': 0.0,
            'total_gst': 0.0,
            'admission_total': 0.0,
            'reservation_total': 0.0,
            'full_course_total': 0.0,
            'pending_count': len(pending_in_range),
            'pending_total': pending_total,
            'pending_amount': sum(p['fee_amount'] for p in pending_in_range),
            'rejected_count': rejected_count,
        }

        batches = {}
        for row in collected:
            amt = row['fee_amount'] or 0.0
            base = row['base_amount'] or 0.0
            gst = row['gst_amount'] or 0.0
            ftype = row['fee_type']

            summary['total_collected'] += amt
            summary['total_base'] += base
            summary['total_gst'] += gst
            if ftype == 'admission':
                summary['admission_total'] += amt
            elif ftype == 'reservation':
                summary['reservation_total'] += amt
            elif ftype == 'full_course':
                summary['full_course_total'] += amt

            if row['batch_id']:
                bid = row['batch_id'][0]
                bname = row['batch_id'][1]
                b = batches.setdefault(bid, {
                    'batch_id': bid, 'batch_name': bname,
                    'total_collected': 0.0, 'admission_total': 0.0,
                    'reservation_total': 0.0, 'full_course_total': 0.0,
                    'gst_total': 0.0, 'count': 0, 'pending_count': 0,
                })
                b['total_collected'] += amt
                b['gst_total'] += gst
                b['count'] += 1
                if ftype == 'admission':
                    b['admission_total'] += amt
                elif ftype == 'reservation':
                    b['reservation_total'] += amt
                elif ftype == 'full_course':
                    b['full_course_total'] += amt

        for row in pending_in_range:
            if row['batch_id']:
                bid = row['batch_id'][0]
                bname = row['batch_id'][1]
                b = batches.setdefault(bid, {
                    'batch_id': bid, 'batch_name': bname,
                    'total_collected': 0.0, 'admission_total': 0.0,
                    'reservation_total': 0.0, 'full_course_total': 0.0,
                    'gst_total': 0.0, 'count': 0, 'pending_count': 0,
                })
                b['pending_count'] += 1

        # ── outstanding balance due, per batch — a current snapshot from
        # student.enrollment, not date-filtered (a balance owed doesn't
        # belong to a particular day) ─────────────────────────────────────
        Enrollment = self.env['student.enrollment'].sudo()
        due_rows = Enrollment.search_read(
            [('due_amount', '>', 0)], ['batch_id', 'due_amount'], limit=None)
        total_balance_due = 0.0
        for row in due_rows:
            total_balance_due += row['due_amount'] or 0.0
            if row['batch_id']:
                bid = row['batch_id'][0]
                bname = row['batch_id'][1]
                b = batches.setdefault(bid, {
                    'batch_id': bid, 'batch_name': bname,
                    'total_collected': 0.0, 'admission_total': 0.0,
                    'reservation_total': 0.0, 'full_course_total': 0.0,
                    'gst_total': 0.0, 'count': 0, 'pending_count': 0,
                    'balance_due': 0.0,
                })
                b.setdefault('balance_due', 0.0)
                b['balance_due'] += row['due_amount'] or 0.0
        for b in batches.values():
            b.setdefault('balance_due', 0.0)
        summary['total_balance_due'] = total_balance_due

        # ── day-wise breakdown (verified_date, one bucket per calendar day) ──
        days = {}
        cursor = d_from
        while cursor <= d_to:
            days[cursor.isoformat()] = {'date': cursor.isoformat(), 'total_collected': 0.0, 'count': 0}
            cursor += timedelta(days=1)
        for row in collected:
            if row['verified_date']:
                day = row['verified_date'].date().isoformat() \
                    if hasattr(row['verified_date'], 'date') else str(row['verified_date'])[:10]
                if day in days:
                    days[day]['total_collected'] += row['fee_amount'] or 0.0
                    days[day]['count'] += 1

        return {
            'date_from': d_from.isoformat(),
            'date_to': d_to.isoformat(),
            'summary': summary,
            'batches': sorted(batches.values(), key=lambda b: -b['total_collected']),
            'daywise': sorted(days.values(), key=lambda d: d['date']),
            'fee_type_labels': FEE_TYPE_LABELS,
        }
