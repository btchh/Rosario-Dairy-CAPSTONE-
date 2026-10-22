"""Compatibility guards for the retired synthetic forecasting experiment.

Actual invoices are the only allowed forecasting evidence. Existing experiment
rows remain archived; these entry points neither read nor update them.
"""
DISABLED_MESSAGE = 'Synthetic forecasting is disabled. Use recorded actual invoices only.'


def load_inputs():
    raise ValueError(DISABLED_MESSAGE)


def refresh_demo():
    raise ValueError(DISABLED_MESSAGE)


def demo_report(run=None):
    return {'status': 'disabled', 'scope': 'demo2026', 'is_test': True,
            'is_live': False, 'forecast': [], 'status_message': DISABLED_MESSAGE}
