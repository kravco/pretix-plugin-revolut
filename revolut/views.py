import hmac
import json
import logging
import requests
from django.shortcuts import get_object_or_404, redirect
from pretix.base.models import Order, OrderPayment
from pretix.multidomain.urlreverse import eventreverse


# borrowed from stripe plugin
def _redirect_to_order(order):
    return redirect(
        eventreverse(
            order.event,
            "presale:event.order",
            kwargs={"order": order.code, "secret": order.secret},
        )
        + ("?paid=yes" if order.status == Order.STATUS_PAID else "")
    )


def revolut_return_view(request, **kwargs):
    # kwargs keys = organizer, event, order_code, payment_id, hash
    logging.info(f"REVOLUT: Return with {kwargs=}")

    payment = get_object_or_404(OrderPayment, id=kwargs["payment_id"])
    order = payment.order
    expected_code = order.code
    if kwargs["order_code"] != expected_code:
        logging.info(
            f'REVOLUT: Return with unexpected code: got={kwargs["order_code"]} expected={expected_code}'
        )
        order.comment = f'Returned from gateway with bad code {kwargs["order_code"]}'
        order.save()
        return _redirect_to_order(order)
    expected_hash = order.tagged_secret(f"plugins:revolut:{payment.id}")
    if not hmac.compare_digest(kwargs["hash"], expected_hash):
        logging.info(
            f'REVOLUT: Return with unexpected hash: got={kwargs["hash"]} expected={expected_hash}'
        )
        order.comment = f'Returned from gateway with bad hash {kwargs["hash"]}'
        order.save()
        return _redirect_to_order(order)
    if "revolut_order_id" not in payment.info_data:
        logging.info("REVOLUT: Missing revolut_order_id")
        order.comment = "Missing revolut_order_id in payment info"
        order.save()
        return _redirect_to_order(order)

    revolut_order_id = payment.info_data["revolut_order_id"]
    logging.info(f"REVOLUT: {revolut_order_id=}")

    try:
        revolut = payment.payment_provider
        # TODO encode correctly
        url = f"{revolut.get_base_api_url(order)}/api/orders/{revolut_order_id}"
        logging.info(f"{url=}")
        headers = {
            "Authorization": f"Bearer {revolut.settings.secret_key}",
            "Accept": "application/json",
            "Revolut-Api-Version": "2024-09-01",
        }
        redacted_headers = revolut._redact_headers(headers)
        logging.info(f"REVOLUT: {redacted_headers=}")
        order_response = requests.get(url=url, headers=headers)
        order_data = order_response.json()
        if "state" in order_data:
            state = order_data["state"]
            logging.info(f"REVOLUT: Order state: {state}")
            order.comment = (
                f'Fetched revolut order {revolut_order_id} has state "{state}"'
            )
            order.save()
            if state == "completed":
                payment.confirm()
            elif state == "cancelled" or state == "failed":
                payment.fail(info=payment.info)
        else:
            logging.info(f"REVOLUT: Missing state in {order_data=}")
            order.comment = f"Missing state in fetched revolut order {revolut_order_id}"
            order.save()
    except Exception as e:
        logging.info(f"REVOLUT: Exception in return {payment.full_id}")
        logging.exception(e)
        payment.info = json.dumps({"exception": str(e)})
        payment.save()

    return _redirect_to_order(order)
