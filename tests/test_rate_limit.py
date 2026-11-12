from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
import unittest

from dbt_bench.prepare import rate_limit_delay


class RateLimitTests(unittest.TestCase):
    def test_retry_after_takes_precedence_over_window(self):
        self.assertEqual(rate_limit_delay({'Retry-After': '120', 'RateLimit': '"resolvers";r=0;t=12'}), 120)

    def test_observed_hf_header_waits_past_reset(self):
        h = Message()
        h['ratelimit'] = '"resolvers";r=0;t=75'
        self.assertEqual(rate_limit_delay(h), 76)

    def test_http_date_and_invalid_headers_do_not_create_retry_storm(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90))
        self.assertGreater(rate_limit_delay({'Retry-After': future}), 85)
        self.assertEqual(rate_limit_delay({'Retry-After': 'invalid'}), 60)
