import requests
from config import GITLAB_URL, GITLAB_TOKEN

HEADERS = {"PRIVATE-TOKEN": GITLAB_TOKEN}


def search_projects(keyword):
    resp = requests.get(
        f"{GITLAB_URL}/api/v4/projects",
        headers=HEADERS,
        params={"search": keyword, "per_page": 5, "order_by": "last_activity_at"},
    )
    resp.raise_for_status()
    return [{"id": p["id"], "name": p["name_with_namespace"], "web_url": p["web_url"]} for p in resp.json()]


def create_issue(project_id, title, description=""):
    params = {"title": title}
    if description:
        params["description"] = description
    resp = requests.post(
        f"{GITLAB_URL}/api/v4/projects/{project_id}/issues",
        headers=HEADERS,
        json=params,
    )
    resp.raise_for_status()
    issue = resp.json()
    return {"id": issue["iid"], "title": issue["title"], "url": issue["web_url"]}


def list_my_issues(state="opened"):
    resp = requests.get(
        f"{GITLAB_URL}/api/v4/issues",
        headers=HEADERS,
        params={"state": state, "scope": "assigned_to_me", "per_page": 10, "order_by": "updated_at"},
    )
    resp.raise_for_status()
    return [{"id": issue["iid"], "title": issue["title"], "project": issue["references"]["full"], "url": issue["web_url"]} for issue in resp.json()]
