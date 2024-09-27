from django.http import HttpResponseRedirect
from pretix.base.models import Order, OrderPayment
from pretix.base.payment import PaymentException
from django.shortcuts import get_object_or_404, redirect
import logging
import hmac
import requests
from pretix.multidomain.urlreverse import eventreverse, build_absolute_uri

# borrowed from stripe plugin
def _redirect_to_order(order):
    return redirect(eventreverse(order.event, 'presale:event.order', kwargs={
        'order': order.code,
        'secret': order.secret
    }) + ('?paid=yes' if order.status == Order.STATUS_PAID else ''))

def revolut_return_view(request, **kwargs):
    # kwargs keys = organizer, event, order, payment, hash
    logging.info(f'RETURN {kwargs=}')

    payment = get_object_or_404(OrderPayment, id=kwargs['payment_id'])
    order = payment.order
    expected_code = order.code
    if kwargs['order_code'] != expected_code:
        logging.info(f'BAD CODE')
        order.comment = f'Returned from gateway with bad code {kwargs["order_code"]}'
        order.save()
        return _redirect_to_order(order)
    expected_hash = order.tagged_secret(f'plugins:revolut:{payment.id}')
    if not hmac.compare_digest(kwargs['hash'], expected_hash):
        logging.info(f'BAD HASH')
        order.comment = f'Returned from gateway with bad hash {kwargs["hash"]}'
        order.save()
        return _redirect_to_order(order)
    if 'revolut_order_id' not in payment.info_data:
        logging.info(f'MISSING revolut_order_id')
        order.comment = 'Missing revolut_order_id in payment info'
        order.save()
        return _redirect_to_order(order)
    
    revolut_order_id = payment.info_data['revolut_order_id'] 
    logging.info(f'{revolut_order_id=}')

    try:
        revolut = payment.payment_provider
        #TODO encode correctly
        url = f'{revolut.base_api_url()}/api/orders/{revolut_order_id}'
        logging.info(f'{url=}')
        headers={
            'Authorization': f'Bearer {revolut.settings.secret_key}',
            'Accept': 'application/json',
            'Revolut-Api-Version': '2024-09-01',
        }
        logging.info(f'{headers=}')
        order_response = requests.get(url=url, headers=headers)
        order_data = order_response.json()
        if 'state' in order_data:
            state = order_data['state']
            logging.info(f'STATE: {state}')
            order.comment = f'Fetched revolut order {revolut_order_id} has state "{state}"'
            order.save()
            if state == 'completed':
                payment.confirm()
            elif state == 'cancelled' or state == 'failed':
                payment.fail(info=payment.info)
        else:
            logging.info(f'MISSING state {order_data}')
            order.comment = f'Missing state in fetched revolut order {revolut_order_id}'
            order.save()
    except Exception as e:
        logging.info('EXCEPTION: ' + str(e))

    return _redirect_to_order(order)
