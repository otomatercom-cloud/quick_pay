{
    'name': 'Quick Pay',
    'version': '19.0.1.0.0',
    'summary': 'Pre-admission payment collection (reservation / admission / course fee) with automated admission on verification',
    'description': """
        Public payment-request portal for prospective students to pay a
        Batch Reservation Fee, Admission Fee, or Full Course Fee before
        their admission is formally processed. Once an Accounts officer
        verifies the uploaded payment slip, the student's admission is
        completed automatically — reusing custom_leads_19's lead and
        student_details_19's enrollment/fee logic rather than duplicating it.
    """,
    'author': 'Ajesh',
    'website': 'https://www.otomater.com',
    'category': 'Education',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'website', 'student_details_19', 'custom_leads_19'],
    'data': [
        'security/quick_pay_security.xml',
        'security/ir.model.access.csv',
        'data/quick_pay_sequence.xml',
        'data/quick_pay_source.xml',
        'wizard/quick_pay_reject_wizard_views.xml',
        'views/quick_pay_bulk_import_views.xml',
        'views/quick_pay_views.xml',
        'views/quick_pay_portal_templates.xml',
        'views/batch_views.xml',
        'report/quick_pay_receipt_report.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'quick_pay/static/src/css/quick_pay_portal.css',
        ],
        'web.assets_backend': [
            'quick_pay/static/src/css/fee_dashboard.css',
            'quick_pay/static/src/js/fee_dashboard.js',
            'quick_pay/static/src/xml/fee_dashboard.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
