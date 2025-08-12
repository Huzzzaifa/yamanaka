from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.http import HttpRequest
from django.conf import settings
from .utils import (
    fetch_sheet_as_rows,
    group_and_aggregate,
    infer_default_columns,
    find_best_metric_column,
    filter_rows_by_value,
)

def signup_view(request):
    # Check if the request method is POST (i.e., form submitted)
    if request.method == 'POST':
        form = UserCreationForm(request.POST)  # Bind form with POST data
        if form.is_valid():  # Validate the form input
            user = form.save()  # Save the new user to the database
            login(request, user)  # Log in the user immediately after account creation
            messages.success(request, 'Account created successfully! Welcome to your profile.')
            return redirect('accounts:profile')  # Redirect to the user's profile page
        else:
            messages.error(request, 'Please fix the errors below.')  # Show error message for invalid form
    else:
        form = UserCreationForm()  # If GET request, create an empty form
    # Render the signup page template with the form context
    return render(request, 'accounts/signup.html', {'form': form})


@login_required
def profile_view(request):
    """
    Displays the profile page for the logged-in user.
    """
    user = request.user  # Get the currently logged-in user
    return render(request, 'accounts/profile.html', {'user': user})


@login_required
def pipelines_view(request: HttpRequest):
    """
    Displays the pipelines page for creating pipelines using data imported from Google Sheets.

    Accepts GET params:
    - sheet_id: Google Sheet ID
    - sheet_name: Worksheet name
    - group_by: Column to group by
    - agg_col: Column to aggregate
    - agg: Aggregation function (sum, count, avg, min, max)
    """
    context = {"user": request.user}

    # Always use the configured default sheet; do not allow overriding via URL
    sheet_id = getattr(settings, "DEFAULT_SHEET_ID", "").strip()
    sheet_name = getattr(settings, "DEFAULT_SHEET_NAME", "").strip()
    group_by = ""
    agg_col = ""
    agg = "sum"
    gid = getattr(settings, "DEFAULT_SHEET_GID", "").strip()

    context.update({"group_by": group_by, "agg_col": agg_col, "agg": agg})

    if sheet_id and (sheet_name or gid):
        try:
            headers, rows = fetch_sheet_as_rows(sheet_id, sheet_name or None, gid or None)
            context["headers"] = headers
            context["rows"] = rows[:200]  # show a preview to keep page light
            if headers and rows:
                # Build a table-style pipelines view like the screenshot
                # Identify columns
                def pick_col(*candidates):
                    for c in candidates:
                        if c in headers:
                            return c
                    return None

                programme_col = pick_col("Program", "Programme", headers[0]) or headers[0]
                company_col = pick_col("Company", "Sponsor", "Product")
                platform_col = pick_col("Platform")
                indication_col = pick_col("Indication")
                stage_col = None
                for h in headers:
                    if "phase" in h.lower() or h.lower() == "stage":
                        stage_col = h
                        break
                endpoint_col = pick_col("EndPoint", "Endpoint", "Event")
                event_type_col = pick_col("Chart Type", "EventType", "Type")
                desc_col = pick_col("Description", "Notes", "Years")

                # Group by programme
                col_idx = {h: i for i, h in enumerate(headers)}
                groups = {}
                for r in rows:
                    name = r[col_idx[programme_col]] if programme_col in col_idx and len(r) > col_idx[programme_col] else ""
                    if not str(name).strip():
                        continue
                    groups.setdefault(name, []).append(r)

                pipeline_rows = []
                # Collect distinct event types for legend
                distinct_types = []
                def push_type(t):
                    nonlocal distinct_types
                    t_str = str(t).strip()
                    if t_str and t_str not in distinct_types:
                        distinct_types.append(t_str)
                for programme, rlist in list(groups.items())[:100]:  # cap for safety
                    def get_first(col_name: str) -> str:
                        if not col_name:
                            return ""
                        idx = col_idx.get(col_name)
                        if idx is None:
                            return ""
                        for rr in rlist:
                            if idx < len(rr) and str(rr[idx]).strip():
                                return str(rr[idx]).strip()
                        return ""

                    company = get_first(company_col)
                    platform = get_first(platform_col)
                    indication = get_first(indication_col)
                    stage = get_first(stage_col)
                    description = get_first(desc_col)

                    # Build endpoints list for markers (with event types)
                    raw_markers = []
                    eidx = col_idx.get(endpoint_col) if endpoint_col else None
                    tidx = col_idx.get(event_type_col) if event_type_col else None
                    if eidx is not None:
                        for rr in rlist:
                            label = rr[eidx] if eidx < len(rr) else ""
                            etype = rr[tidx] if (tidx is not None and tidx < len(rr)) else ""
                            if str(label).strip():
                                raw_markers.append({"label": str(label).strip(), "type": str(etype).strip()})
                                push_type(etype)
                    # deduplicate by (label, type) while preserving order
                    seen_lt = set()
                    uniq_markers = []
                    for mk in raw_markers:
                        key = (mk.get("label", ""), mk.get("type", ""))
                        if key not in seen_lt:
                            seen_lt.add(key)
                            uniq_markers.append(mk)
                    n = max(1, len(uniq_markers))
                    markers = []
                    for i, mk in enumerate(uniq_markers[:20]):
                        mk_copy = dict(mk)
                        mk_copy["pos"] = round(((i + 1) / (n + 1)) * 100, 2)
                        markers.append(mk_copy)

                    pipeline_rows.append(
                        {
                            "programme": programme,
                            "company": company,
                            "platform": platform,
                            "indication": indication,
                            "stage": stage,
                            "description": description,
                            "markers": markers,
                        }
                    )

                # Assign colors to event types
                palette = [
                    "#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#f1c40f",
                    "#e67e22", "#1abc9c", "#34495e", "#fd79a8", "#55efc4",
                ]
                type_colors = {}
                for idx, t in enumerate(distinct_types):
                    type_colors[t] = palette[idx % len(palette)]
                # annotate markers with colors
                for prow in pipeline_rows:
                    for mk in prow.get("markers", []):
                        mk["color"] = type_colors.get(mk.get("type", ""), "#666")
                context["pipeline_rows"] = pipeline_rows
                context["event_types"] = distinct_types
                context["event_type_colors"] = type_colors
                context["event_types_list"] = [
                    {"name": t, "color": type_colors.get(t, "#666")} for t in distinct_types
                ]
        except Exception as exc:
            context["error"] = str(exc)

    return render(request, 'accounts/pipelines.html', context)


@login_required
def graph_view(request: HttpRequest):
    """
    Displays the graph view page for visualizing data and analytics.

    Accepts same GET params as pipelines_view.
    """
    context = {"user": request.user}

    # Always use the configured default sheet; do not allow overriding via URL
    sheet_id = getattr(settings, "DEFAULT_SHEET_ID", "").strip()
    sheet_name = getattr(settings, "DEFAULT_SHEET_NAME", "").strip()
    group_by = ""
    agg_col = ""
    agg = "sum"
    gid = getattr(settings, "DEFAULT_SHEET_GID", "").strip()

    context.update({"group_by": group_by, "agg_col": agg_col, "agg": agg})

    if sheet_id and (sheet_name or gid):
        try:
            headers, rows = fetch_sheet_as_rows(sheet_id, sheet_name or None, gid or None)
            context["headers"] = headers
            if headers and rows:
                # Follow same logic as pipelines, but build one combined chart: Program vs best metric across all rows
                label_column = "Program" if "Program" in headers else headers[0]
                preferred_metrics = [
                    "CBR (%)",
                    "CFB (%)",
                    "CFB (Absolute)",
                    "CBR (Absolute)",
                    "Response",
                ]
                metric_column = find_best_metric_column(headers, rows, preferred_metrics)
                aggregated = group_and_aggregate(headers, rows, label_column, metric_column or label_column, "avg")
                labels = [item["group"] for item in aggregated]
                data_values = [item["metric"] for item in aggregated]
                context.update({
                    "chart_labels": labels,
                    "chart_values": data_values,
                    "chart_y_label": metric_column or "Value",
                })
        except Exception as exc:
            context["error"] = str(exc)

    return render(request, 'accounts/graph_view.html', context)
