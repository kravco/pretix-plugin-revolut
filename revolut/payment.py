import json
import logging
import requests
from collections import OrderedDict
from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy
from pretix.base.payment import BasePaymentProvider, PaymentException
from pretix.multidomain.urlreverse import build_absolute_uri


class Revolut(BasePaymentProvider):
    identifier = "revolut"
    verbose_name = "Card payment"

    execute_payment_needs_user = True

    test_mode_message = (
        "While in test mode, all API requests will be done to sandbox environment"
    )

    @property
    def settings_form_fields(self):
        after = "_enabled"
        fields = super().settings_form_fields
        insert = {
            "public_key": forms.CharField(
                label=gettext_lazy("Public Key"),
                required=True,
                help_text=gettext_lazy(
                    "Your Revolut API public key (should have pk_ prefix)."
                ),
            ),
            "secret_key": forms.CharField(
                label=gettext_lazy("Secret Key"),
                required=True,
                widget=forms.PasswordInput(),
                help_text=gettext_lazy(
                    "Your Revolut API secret key (should have sk_ prefix, does not display saved value)."
                ),
            ),
        }
        if after in fields:
            temp = []
            for id, field in fields.items():
                temp.append((id, field))
                if id == after:
                    for id, field in insert.items():
                        temp.append((id, field))
            return OrderedDict(temp)
        fields.update(insert)
        return fields

    def payment_is_valid_session(self, request):
        # we do not check for any information in request (for now)
        return True

    def execute_payment(self, request, payment):
        return_url = build_absolute_uri(
            self.event,
            "plugins:revolut:return",
            kwargs={
                "order_code": payment.order.code,
                "payment_id": payment.pk,
                "hash": payment.order.tagged_secret(f"plugins:revolut:{payment.pk}"),
            },
        )
        logging.info(f"REVOLUT: {return_url=}")

        # TODO temporary replacement for development
        return_url = return_url.replace("http://localhost:8000", "https://example.com")

        try:
            url = f"{self.get_base_api_url(payment.order)}/api/orders"
            logging.info(f"REVOLUT: {url=}")
            data = json.dumps(
                {
                    "amount": self._decimal_to_int(payment.amount),
                    "currency": payment.order.event.currency,
                    "description": f"Pretix order {payment.full_id}",
                    "redirect_url": return_url,
                    "merchant_order_data": {
                        "reference": payment.pk,
                    },
                }
            )
            logging.info(f"REVOLUT: {data=}")
            headers = {
                "Authorization": f"Bearer {self.settings.secret_key}",
                "Content-Type": "application/json",
                "Revolut-Api-Version": "2024-09-01",
            }
            redacted_headers = self._redact_headers(headers)
            logging.info(f"REVOLUT: {redacted_headers=}")
            order_response = requests.post(url=url, data=data, headers=headers)
            order_data = order_response.json()
            logging.info(f'REVOLUT: Created order with id: {order_data["id"]}')
            logging.info(
                f'REVOLUT: Redirecting to payment url: {order_data["checkout_url"]}'
            )
            payment.info = json.dumps({"revolut_order_id": order_data["id"]})
            payment.save()
            return order_data["checkout_url"]
        except Exception as e:
            logging.info(f"REVOLUT: Exception in payment {payment.full_id}")
            logging.exception(e)
            payment.info = json.dumps({"exception": str(e)})
            payment.save()
            raise PaymentException("Payment failed")

    def checkout_confirm_render(self, request, order=None, info_data=None):
        return gettext_lazy(
            "You will be redirected to payment page, there you input card details and complete the purchase"
        )

    def get_base_api_url(self, order=None):
        testmode = self.event.testmode if order is None else order.testmode
        return (
            "https://sandbox-merchant.revolut.com"
            if testmode
            else "https://merchant.revolut.com"
        )

    def _redact_headers(self, headers):
        redacted_headers = dict(headers)
        if (
            "Authorization" in headers
            and self.settings.secret_key in headers["Authorization"]
        ):
            redacted = type(self.settings.secret_key).__name__
            if redacted == "str":
                redacted = redacted + f" len={len(self.settings.secret_key)}"
            redacted_headers["Authorization"] = redacted_headers[
                "Authorization"
            ].replace(self.settings.secret_key, f"[redacted {redacted}]")
        return redacted_headers

    # borrowed from stripe plugin
    def _decimal_to_int(self, amount):
        places = settings.CURRENCY_PLACES.get(self.event.currency, 2)
        return int(amount * 10**places)
