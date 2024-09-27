from pretix.base.payment import BasePaymentProvider, PaymentException
from django.utils.translation import gettext_lazy
from django import forms
import logging
from collections import OrderedDict
import requests
import json
from django.conf import settings
from django.urls import reverse
from pretix.multidomain.urlreverse import eventreverse, build_absolute_uri

class Revolut(BasePaymentProvider):
    identifier = 'revolut'
    verbose_name = 'Card payment'

    execute_payment_needs_user = True

    test_mode_message = 'While in test mode, all API requests will be done to sandbox environment'

    @property
    def settings_form_fields(self):
        after = '_enabled'
        fields = super().settings_form_fields
        insert = {
            'public_key': forms.CharField(
                label=gettext_lazy('Public Key'),
                required=True,
                help_text=gettext_lazy('Your Revolut API public key (should have pk_ prefix).'),
            ),
            'secret_key': forms.CharField(
                label=gettext_lazy('Secret Key'),
                required=True,
                widget=forms.PasswordInput(),
                help_text=gettext_lazy('Your Revolut API secret key (should have sk_ prefix).'),
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
        #NOTE use this when finished: build_absolute_uri
        #TODO escape this please
#        redirect_url = 'https://mock.kravjar.sk/revolut-return/' + str(payment.id)
#        eventreverse(self.event, 'presale:event.order.pay.confirm', kwargs={
#            'order': payment.order.code,
#            'secret': payment.order.secret,
#            'payment': payment.id,
#        })
        return_url = build_absolute_uri(self.event, 'plugins:revolut:return', kwargs={
            'order_code': payment.order.code,
            'payment_id': payment.pk,
            'hash': payment.order.tagged_secret(f'plugins:revolut:{payment.pk}'),
        })

        logging.info(return_url)
        #TODO temporary
        return_url = return_url.replace('http://localhost:8000', 'https://mock.kravjar.sk')

        try:
            url = f'{self.base_api_url()}/api/orders'
            logging.info(f'{url=}')
            data=json.dumps({
                'amount': self._decimal_to_int(payment.amount),
                'currency': payment.order.event.currency,
                'description': f'Pretix order {payment.full_id}',
                'redirect_url': return_url,
                'merchant_order_data': {
                    'reference': payment.pk,
                }
            })
            logging.info(f'{data=}')
            headers={
                'Authorization': f'Bearer {self.settings.secret_key}',
                'Content-Type': 'application/json',
                'Revolut-Api-Version': '2024-09-01',
            }
            logging.info(f'{headers=}')
            order_response = requests.post(url=url, data=data, headers=headers)
            order_data = order_response.json()
            logging.info(f'Order created: {order_data["id"]}')
            logging.info(f'Redirecting to url: {order_data["checkout_url"]}')
            payment.info = json.dumps({'revolut_order_id': order_data['id']})
            payment.save()
            return order_data['checkout_url']
        except Exception as e:
            logging.exception(e)
            payment.info = json.dumps({'exception': str(e)})
            payment.save()
            raise PaymentException('Payment failed')


    def checkout_confirm_render(self, request, order = None, info_data = None):
        event_test_mode = self.event.testmode # better would be order.testmode
        logging.info(f"{event_test_mode=}")
        order_test_mode = 'None' if order is None else type(order)
        logging.info(f"{order_test_mode=}")
        return gettext_lazy('You will be redirected to payment page, where you input card data and complete the purchase')

    def base_api_url(self):
        return 'https://sandbox-merchant.revolut.com' if self.event.testmode else 'https://merchant.revolut.com'

    # borrowed from stripe plugin
    def _decimal_to_int(self, amount):
        places = settings.CURRENCY_PLACES.get(self.event.currency, 2)
        return int(amount * 10 ** places)
    