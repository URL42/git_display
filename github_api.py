"""
github_api.py — GitHub data fetcher
  - GraphQL for contribution calendar (not available on REST)
  - REST for recent repos and activity feed
"""

import requests
from datetime import datetime, timezone
from typing import Optional

GRAPHQL_URL = "https://api.github.com/graphql"
REST_BASE   = "https://api.github.com"

CONTRIB_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


class GitHubClient:
    def __init__(self, token: str, username: str):
        self.username = username
        self.session  = requests.Session()
        self.session.headers.update({
            "Authorization": f"bearer {token}",
            "Accept":        "application/vnd.github.v3+json",
            "User-Agent":    "epaper-dashboard/1.0",
        })

    # ── Contribution calendar (GraphQL) ─────────────────────────────────────
    def get_contribution_calendar(self) -> dict:
        """
        Returns:
          {
            "totalContributions": int,
            "weeks": [
              { "contributionDays": [
                  { "date": "2024-04-21", "contributionCount": 3 },
                  ...  (7 items, Mon–Sun)
              ]},
              ...  (52+ weeks)
            ]
          }
        """
        resp = self.session.post(
            GRAPHQL_URL,
            json={"query": CONTRIB_QUERY, "variables": {"login": self.username}},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {data['errors']}")

        return (data["data"]["user"]
                    ["contributionsCollection"]
                    ["contributionCalendar"])

    # ── Recent repos (REST) ─────────────────────────────────────────────────
    def get_recent_repos(self, limit: int = 7) -> list:
        """Returns repos sorted by most recently pushed."""
        resp = self.session.get(
            f"{REST_BASE}/users/{self.username}/repos",
            params={"sort": "pushed", "per_page": limit * 2, "type": "owner"},
            timeout=15,
        )
        resp.raise_for_status()

        repos = []
        for r in resp.json():
            repos.append({
                "name":        r["name"],
                "stars":       r["stargazers_count"],
                "language":    r["language"] or "—",
                "pushed_at":   r["pushed_at"],
                "description": (r["description"] or "").strip(),
                "private":     r["private"],
                "fork":        r["fork"],
            })
            if len(repos) >= limit:
                break

        return repos

    # ── Activity feed (REST) ─────────────────────────────────────────────────
    def get_activity_feed(self, limit: int = 7) -> list:
        """Returns parsed public events from the user's received_events feed."""
        resp = self.session.get(
            f"{REST_BASE}/users/{self.username}/events",
            params={"per_page": 50},
            timeout=15,
        )
        resp.raise_for_status()

        parsed = []
        for event in resp.json():
            item = _parse_event(event)
            if item:
                parsed.append(item)
            if len(parsed) >= limit:
                break

        return parsed


# ── Event parser ─────────────────────────────────────────────────────────────
def _parse_event(event: dict) -> Optional[dict]:
    etype   = event.get("type", "")
    repo    = event.get("repo", {}).get("name", "?")
    created = event.get("created_at", "")
    payload = event.get("payload", {})

    desc = None

    if etype == "PushEvent":
        commits = payload.get("commits", [])
        branch  = payload.get("ref", "refs/heads/main").split("/")[-1]
        if commits:
            msg  = commits[0]["message"].split("\n")[0]
            desc = f"→ {branch}: {_trunc(msg, 44)}"
        else:
            desc = f"→ pushed to {branch}"

    elif etype == "CreateEvent":
        ref_type = payload.get("ref_type", "branch")
        ref      = payload.get("ref") or ""
        desc = f"Created {ref_type} {_trunc(ref, 30)}" if ref else f"Created {ref_type}"

    elif etype == "DeleteEvent":
        ref_type = payload.get("ref_type", "branch")
        ref      = payload.get("ref") or ""
        desc = f"Deleted {ref_type} {_trunc(ref, 30)}"

    elif etype == "PullRequestEvent":
        action = payload.get("action", "")
        pr     = payload.get("pull_request", {})
        num    = pr.get("number", "?")
        title  = _trunc(pr.get("title", ""), 38)
        desc   = f"PR #{num} {action}: {title}"

    elif etype == "IssuesEvent":
        action = payload.get("action", "")
        issue  = payload.get("issue", {})
        num    = issue.get("number", "?")
        title  = _trunc(issue.get("title", ""), 38)
        desc   = f"Issue #{num} {action}: {title}"

    elif etype == "IssueCommentEvent":
        issue  = payload.get("issue", {})
        num    = issue.get("number", "?")
        desc   = f"Commented on #{num}"

    elif etype == "WatchEvent":
        desc = "Starred"

    elif etype == "ForkEvent":
        forkee = payload.get("forkee", {}).get("full_name", repo)
        desc   = f"Forked → {_trunc(forkee, 40)}"

    elif etype == "ReleaseEvent":
        action = payload.get("action", "")
        tag    = payload.get("release", {}).get("tag_name", "")
        desc   = f"Release {action}: {_trunc(tag, 30)}"

    elif etype == "PublicEvent":
        desc = "Made public"

    else:
        return None  # skip unrecognised/noisy events

    return {
        "repo":        repo,
        "repo_short":  repo.split("/")[-1],
        "description": desc,
        "created_at":  created,
        "type":        etype,
    }


# ── Utility ──────────────────────────────────────────────────────────────────
def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def relative_time(iso_str: str) -> str:
    """Convert ISO 8601 timestamp → human-readable relative string."""
    try:
        dt  = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        s   = int((now - dt).total_seconds())

        if s <  60:    return "just now"
        if s <  3600:  return f"{s // 60}m ago"
        if s <  86400: return f"{s // 3600}h ago"
        return             f"{s // 86400}d ago"
    except Exception:
        return "?"
