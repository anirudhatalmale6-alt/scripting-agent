"""
agent/log_filter.py
───────────────────
Suppresses noisy Flask/Werkzeug health check logs (GET / 200).
Import and call setup_log_filter() in each Flask agent.
"""
import logging


class _HealthCheckFilter(logging.Filter):
    """Drop 'GET / HTTP' 200 lines — these are just Docker healthcheck pings."""
    def filter(self, record):
        msg = record.getMessage()
        return not ("GET / HTTP" in msg and "200" in msg)


def setup_log_filter():
    """Attach filter to werkzeug logger to suppress health check noise."""
    wz = logging.getLogger("werkzeug")
    wz.addFilter(_HealthCheckFilter())
