"""Ensemble des checks de sante d'un site (uptime, ssl, ndd, pagespeed, stack, backup)."""
from .uptime import check_uptime
from .ssl_check import check_ssl
from .domain import check_domain
from .pagespeed import check_pagespeed
from .stack import check_stack
from .backup import load_backup_status

__all__ = [
    "check_uptime",
    "check_ssl",
    "check_domain",
    "check_pagespeed",
    "check_stack",
    "load_backup_status",
]
