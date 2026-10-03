from django.core.management.base import BaseCommand, CommandError

from planning.reservation_integrity import find_active_reservation_overlaps


class Command(BaseCommand):
    help = "Read-only check for overlapping ACTIVE physical-mold reservations."

    def handle(self, *args, **options):
        conflicts = find_active_reservation_overlaps()
        if not conflicts:
            self.stdout.write(self.style.SUCCESS("No overlapping ACTIVE reservations found."))
            return
        for conflict in conflicts:
            self.stdout.write(
                "mold={mold_id}/{mold_code} reservations={left_id},{right_id} "
                "ranges={left_start}..{left_end},{right_start}..{right_end} "
                "statuses={left_status},{right_status} revisions={left_revision},{right_revision} "
                "execution_paths={left_path},{right_path}".format(
                    mold_id=conflict.physical_mold_id,
                    mold_code=conflict.physical_mold_code,
                    left_id=conflict.left_id,
                    right_id=conflict.right_id,
                    left_start=conflict.left_start,
                    left_end=conflict.left_end,
                    right_start=conflict.right_start,
                    right_end=conflict.right_end,
                    left_status=conflict.left_status,
                    right_status=conflict.right_status,
                    left_revision=conflict.left_revision_id,
                    right_revision=conflict.right_revision_id,
                    left_path=conflict.left_execution_path_id,
                    right_path=conflict.right_execution_path_id,
                )
            )
        raise CommandError(
            f"Found {len(conflicts)} overlapping ACTIVE reservation pair(s)."
        )
