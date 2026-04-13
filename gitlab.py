import requests
from config import GITLAB_URL, GITLAB_TOKEN, GITLAB_USERNAME

HEADERS = {"PRIVATE-TOKEN": GITLAB_TOKEN}
_user_id = None


def get_my_user_id():
    global _user_id
    if _user_id is None:
        resp = requests.get(f"{GITLAB_URL}/api/v4/users", headers=HEADERS, params={"username": GITLAB_USERNAME})
        resp.raise_for_status()
        users = resp.json()
        if users:
            _user_id = users[0]["id"]
    return _user_id


def search_projects(keyword):
    resp = requests.get(
        f"{GITLAB_URL}/api/v4/projects",
        headers=HEADERS,
        params={"search": keyword, "per_page": 5, "order_by": "last_activity_at"},
    )
    resp.raise_for_status()
    return [{"id": p["id"], "name": p["name_with_namespace"], "web_url": p["web_url"]} for p in resp.json()]


def create_issue(project_id, title, description="", estimate=""):
    params = {"title": title, "assignee_ids": [get_my_user_id()]}
    if description:
        params["description"] = description
    resp = requests.post(
        f"{GITLAB_URL}/api/v4/projects/{project_id}/issues",
        headers=HEADERS,
        json=params,
    )
    resp.raise_for_status()
    issue = resp.json()
    result = {"id": issue["iid"], "title": issue["title"], "url": issue["web_url"]}
    if estimate:
        requests.post(
            f"{GITLAB_URL}/api/v4/projects/{project_id}/issues/{issue['iid']}/time_estimate",
            headers=HEADERS,
            json={"duration": estimate},
        )
        result["estimate"] = estimate
    return result


def sync_my_issues():
    """Pull all assigned open issues with triage-relevant fields."""
    resp = requests.get(
        f"{GITLAB_URL}/api/v4/issues",
        headers=HEADERS,
        params={"state": "opened", "scope": "assigned_to_me", "per_page": 50, "order_by": "updated_at"},
    )
    resp.raise_for_status()
    issues = []
    for issue in resp.json():
        labels = [l.lower() for l in issue.get("labels", [])]
        tier = "self"
        if any(l in labels for l in ("urgent", "critical", "blocker", "priority")):
            tier = "negotiable"
        time_est_seconds = issue.get("time_stats", {}).get("time_estimate", 0)
        time_est_minutes = time_est_seconds // 60 if time_est_seconds else None
        issues.append({
            "iid": issue["iid"],
            "project_id": issue.get("project_id"),
            "title": issue["title"],
            "description": (issue.get("description") or "")[:200],
            "due_date": issue.get("due_date"),
            "time_estimate": time_est_minutes,
            "tier": tier,
            "url": issue["web_url"],
            "source_id": f"{issue.get('project_id')}_{issue['iid']}",
        })
    return issues


def list_my_issues(state="opened"):
    resp = requests.get(
        f"{GITLAB_URL}/api/v4/issues",
        headers=HEADERS,
        params={"state": state, "scope": "assigned_to_me", "per_page": 10, "order_by": "updated_at"},
    )
    resp.raise_for_status()
    return [{"id": issue["iid"], "title": issue["title"], "project": issue["references"]["full"], "url": issue["web_url"]} for issue in resp.json()]
