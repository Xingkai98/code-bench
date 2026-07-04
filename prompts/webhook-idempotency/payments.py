"""Payment webhook processor template."""


class PaymentProcessor:
    def __init__(self, expected_amounts=None):
        raise NotImplementedError

    def handle_event(self, event):
        raise NotImplementedError

    def get_order(self, order_id):
        raise NotImplementedError
