from django.urls import path

from . import views

urlpatterns = [
    path("execution-paths/", views.execution_path_list, name="execution_path_list"),
    path(
        "execution-paths/<int:path_id>/",
        views.execution_path_detail,
        name="execution_path_detail",
    ),
    path(
        "execution-paths/<int:path_id>/entries/",
        views.execution_path_entry_create,
        name="execution_path_entry_create",
    ),
    path(
        "execution-paths/<int:path_id>/temporary-stop/",
        views.execution_path_temporary_stop,
        name="execution_path_temporary_stop",
    ),
    path(
        "execution-paths/<int:path_id>/resume/",
        views.execution_path_resume,
        name="execution_path_resume",
    ),
    path(
        "execution-paths/<int:path_id>/full-stop/",
        views.execution_path_full_stop,
        name="execution_path_full_stop",
    ),
    path(
        "execution-paths/<int:path_id>/statistics-complete/",
        views.execution_path_statistics_complete,
        name="execution_path_statistics_complete",
    ),
    path(
        "execution-paths/<int:path_id>/target-revisions/request/",
        views.target_revision_request,
        name="target_revision_request",
    ),
    path(
        "execution-paths/<int:path_id>/target-revisions/scrap-compensation/",
        views.target_revision_scrap_request,
        name="target_revision_scrap_request",
    ),
    path(
        "target-revisions/<int:request_id>/decide/",
        views.target_revision_decide,
        name="target_revision_decide",
    ),
    path("", views.production_list, name="production_list"),
    path("programs/", views.program_list, name="program_list"),
    path("history/", views.production_history, name="production_history"),
    path("history/conflicts/", views.production_conflicts, name="production_conflicts"),
    path(
        "history/conflicts/resolve/",
        views.production_conflict_resolve,
        name="production_conflict_resolve",
    ),
    path(
        "history/conflicts/<slug:kind>/",
        views.production_conflicts_kind,
        name="production_conflicts_kind",
    ),
    path("history/<int:pk>/", views.production_history_detail, name="production_history_detail"),
    path(
        "history/archive/<int:pk>/",
        views.production_history_archive_detail,
        name="production_history_archive_detail",
    ),
    path("programs/<int:pk>/status/", views.program_status, name="program_status"),
    path("programs/<int:program_pk>/entry/", views.entry_create, name="entry_create"),
    path("entries/<int:pk>/edit/", views.entry_edit, name="entry_edit"),
    path("pipes/new/", views.pipe_create, name="pipe_create"),
    path("pipes/<int:pk>/edit/", views.pipe_edit, name="pipe_edit"),
]
