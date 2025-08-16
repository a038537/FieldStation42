# File: fs42/overlays/send_infobar.py
# Purpose: CLI to test the infobar without touching the player.
# Usage example:
#   python3 -m fs42.overlays.send_infobar --num 3 --name "TV3 Stockholm" \
#     --title "Mamma Mia!" --mins 138

import argparse
from datetime import datetime, timedelta
from .bridge import send_infobar_event


def cli():
    p = argparse.ArgumentParser(description="Send a test infobar event")
    p.add_argument("--num", type=int, required=True, help="Channel number")
    p.add_argument("--name", type=str, required=True, help="Channel name")
    p.add_argument("--title", type=str, required=True, help="Programme title")
    p.add_argument("--mins", type=int, default=60, help="Minutes remaining")
    args = p.parse_args()

    now = datetime.utcnow()
    end = now + timedelta(minutes=args.mins)
    send_infobar_event(args.num, args.name, args.title, start=now, end=end)


if __name__ == "__main__":
    cli()
