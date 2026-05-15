"""
One-time feedback + share request email to all signed-in users.

Run from the project root:
    python scripts/send_feedback_email.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from db.database import get_all_users_with_email
from services.email import _send

SUBJECT = "Quick question about BrawlIQ 👋"


def _body(username: str) -> str:
    return (
        f"Hi {username},\n\n"
        "You're one of the first people to sign in to BrawlIQ — thank you!\n\n"
        "I'd love to know what you think. Is it useful? Anything broken or confusing? "
        "Any features you wish it had?\n\n"
        "Just reply to this email — I read every message.\n\n"
        "And if you're enjoying it, the best way to help is to share it with a friend "
        "or clanmate. It's free, no login needed to look up any player:\n\n"
        "→ https://brawliq.fly.dev\n\n"
        "Thanks for being an early supporter 🙏\n\n"
        "— Marco (BrawlIQ)"
    )


def main() -> None:
    users = get_all_users_with_email()
    if not users:
        print("No users with email found.")
        return

    print(f"Sending to {len(users)} user(s)...\n")
    for u in users:
        ok = _send(u["email"], SUBJECT, _body(u["username"]))
        status = "✓" if ok else "✗ FAILED"
        print(f"  {status}  {u['username']} <{u['email']}>")

    print("\nDone.")


if __name__ == "__main__":
    main()
