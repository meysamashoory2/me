"""PostgreSQL expressions and stable identifiers for mold reservations."""

from django.contrib.postgres.fields import DateRangeField, RangeBoundary
from django.db.models import Func


ACTIVE_MOLD_RESERVATION_CONSTRAINT = "exclude_active_mold_reservation_overlap"


class InclusiveDateRange(Func):
    """Build PostgreSQL ``daterange(start, end, '[]')`` evidence."""

    function = "DATERANGE"
    output_field = DateRangeField()

    def __init__(self, start_expression, end_expression):
        super().__init__(
            start_expression,
            end_expression,
            RangeBoundary(inclusive_lower=True, inclusive_upper=True),
        )
