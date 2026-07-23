from django.urls import path

from . import control, views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # Control panel
    path("control/", control.ControlHubView.as_view(), name="control_hub"),
    path("control/orgs/", control.org_list, name="control_orgs"),
    path("control/orgs/new/", control.org_create, name="control_org_create"),
    path(
        "control/orgs/<int:org_id>/policy/",
        control.org_policy_edit,
        name="control_org_policy",
    ),
    path("control/users/", control.user_list, name="control_users"),
    path("control/users/new/", control.user_create, name="control_user_create"),
    path(
        "control/users/<int:user_id>/quota/",
        control.user_quota_edit,
        name="control_user_quota",
    ),
]
