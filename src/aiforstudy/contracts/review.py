"""Status vocabulary for media verdicts and their aggregate reports.

Evidence-resolution tickets use their own needs_evidence contract. A blocked
report records an unavailable check; an individual verdict cannot be blocked.
"""

MEDIA_VERDICT_STATUSES = ('pass', 'fail', 'needs_review')
MEDIA_REPORT_STATUSES = (*MEDIA_VERDICT_STATUSES, 'blocked')
