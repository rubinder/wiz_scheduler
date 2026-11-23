"""Approve or decline a company's AI host call capacity request (decision 103).

Approving sets the company's simultaneous-call allocation (default: what it
asked for), raises the Amazon Connect quota if the new allocations need it
(a Service Quotas request, or, above VOICE_QUOTA_AUTO_REQUEST_MAX, a
support-case text mailed to the operator) and emails the company's admins.
Declining emails them too. The request email the operator received names the
request id.

Run as a one-off ECS task (it needs the task role for Service Quotas):
    python -m backend.scripts.approve_capacity_request <request_id> [--allocation N]
    python -m backend.scripts.approve_capacity_request <request_id> --decline
"""
import argparse
import asyncio
import json
import logging
import sys
from typing import Any

logging.basicConfig(level=logging.INFO)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("request_id")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--allocation", type=int, default=None,
                       help="simultaneous calls to grant (default: the number asked for)")
    group.add_argument("--decline", action="store_true")
    return p


async def handle(db: Any, argv: list[str], client: Any = None) -> dict:
    from backend.reservations import voice_capacity

    args = _parser().parse_args(argv)
    if args.decline:
        return await voice_capacity.decline_request(db, args.request_id)
    return await voice_capacity.approve_request(db, args.request_id, args.allocation,
                                                client=client)


async def main(argv: list[str]) -> int:
    from backend.database import async_session_factory
    from backend.reservations.voice_capacity import CapacityRequestError

    async with async_session_factory() as db:
        try:
            out = await handle(db, argv)
        except CapacityRequestError as e:
            print(str(e), file=sys.stderr)
            return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
