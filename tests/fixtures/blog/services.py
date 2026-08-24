import requests
from django.conf import settings


class PaymentService:
    def charge(self, amount):
        # an external call hidden inside a service — Prism should find this via one-hop follow,
        # and resolve the destination to the settings name (not a literal host).
        return requests.post(settings.PAYMENT_URL, data={"amount": amount}, timeout=10)


class CheckoutService:
    def complete(self, order):
        # the outbound call is TWO hops from the handler: handler → complete() → _notify()
        self._notify(order)

    def _notify(self, order):
        return requests.post(settings.WEBHOOK_URL, data={"id": order.id}, timeout=5)
