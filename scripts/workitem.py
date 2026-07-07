from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update OpenProgram work items.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENPROGRAM_API_BASE_URL", "http://localhost:8000"),
        help="Base URL for the OpenProgram API.",
    )
    parser.add_argument(
        "--authorization",
        default=os.environ.get("OPENPROGRAM_AUTHORIZATION"),
        help="Authorization header value, if needed.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a work item with an explicit id.")
    _add_create_args(create)

    branch = subparsers.add_parser("branch", help="Create a work item from a branch name.")
    branch.add_argument("--repo", required=True)
    branch.add_argument("--branch", required=True)
    branch.add_argument("--name")
    branch.add_argument("--item-type", default="feature")
    branch.add_argument("--workstream-id")
    branch.add_argument("--metadata", default="{}")

    pull_request = subparsers.add_parser("pr", help="Create a work item from a pull request.")
    pull_request.add_argument("--repo", required=True)
    pull_request.add_argument("--pr-id", required=True)
    pull_request.add_argument("--title", required=True)
    pull_request.add_argument("--item-type", default="feature")
    pull_request.add_argument("--workstream-id")
    pull_request.add_argument("--metadata", default="{}")

    transition = subparsers.add_parser("transition", help="Transition a work item state.")
    transition.add_argument("--id", required=True)
    transition.add_argument("--state", required=True)

    link = subparsers.add_parser("link", help="Link a work item to a workstream.")
    link.add_argument("--workstream-id", required=True)
    link.add_argument("--work-item-id", required=True)

    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    authorization = args.authorization

    try:
        payload = _payload_for_args(args)
        path = _path_for_args(args)
        method = _method_for_args(args)
        result = _request_json(base_url, path, method, payload, authorization)
    except (ValueError, HTTPError, URLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _add_create_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--state", default="proposed")
    parser.add_argument("--item-type", default="feature")
    parser.add_argument("--repo")
    parser.add_argument("--branch")
    parser.add_argument("--pr-id")
    parser.add_argument("--workstream-id")
    parser.add_argument("--metadata", default="{}")


def _payload_for_args(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "create":
        return {
            "id": args.id,
            "name": args.name,
            "state": args.state,
            "item_type": args.item_type,
            "repo": args.repo,
            "branch": args.branch,
            "pr_id": args.pr_id,
            "workstream_id": args.workstream_id,
            "metadata": _json_object(args.metadata),
        }
    if args.command == "branch":
        return {
            "repo": args.repo,
            "branch": args.branch,
            "name": args.name,
            "item_type": args.item_type,
            "workstream_id": args.workstream_id,
            "metadata": _json_object(args.metadata),
        }
    if args.command == "pr":
        return {
            "repo": args.repo,
            "pr_id": args.pr_id,
            "title": args.title,
            "item_type": args.item_type,
            "workstream_id": args.workstream_id,
            "metadata": _json_object(args.metadata),
        }
    if args.command == "transition":
        return {"new_state": args.state}
    if args.command == "link":
        return {}
    raise ValueError(f"unsupported command: {args.command}")


def _path_for_args(args: argparse.Namespace) -> str:
    if args.command == "create":
        return "/config/work-items"
    if args.command == "branch":
        return "/config/work-items/from-branch"
    if args.command == "pr":
        return "/config/work-items/from-pr"
    if args.command == "transition":
        return f"/config/work-items/{args.id}/transition"
    if args.command == "link":
        return f"/config/workstreams/{args.workstream_id}/work-items/{args.work_item_id}"
    raise ValueError(f"unsupported command: {args.command}")


def _method_for_args(args: argparse.Namespace) -> str:
    return "POST"


def _request_json(
    base_url: str,
    path: str,
    method: str,
    payload: dict[str, object],
    authorization: str | None,
) -> object:
    data = json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json"}
    if authorization:
        headers["authorization"] = authorization
    request = Request(f"{base_url}{path}", data=data, method=method, headers=headers)
    with urlopen(request) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else None


def _json_object(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be a JSON object")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
