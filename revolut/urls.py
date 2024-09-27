from django.urls import include, re_path

from .views import revolut_return_view

event_patterns = [
    re_path(
        r"^revolut/",
        include(
            [
                re_path(
                    r"^return/(?P<order_code>[^/]+)/(?P<payment_id>[0-9]+)/(?P<hash>[^/]+)/$",
                    revolut_return_view,
                    name="return",
                ),
            ]
        ),
    ),
]
