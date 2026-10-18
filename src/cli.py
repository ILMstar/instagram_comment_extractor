"""Command-line interface for Instagram comment username extractor."""

import argparse
from pathlib import Path
import sys
from typing import Sequence

# Ensure project root is in sys.path when launched from other directories
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.exporter import write_usernames_file
from src.extractor import CommentProvider, extract_usernames
from src.url_parser import InvalidInstagramURLError, parse_instagram_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Instagram comment usernames to a .txt file.",
    )
    parser.add_argument(
        "url",
        help="Instagram Post, Reel, or TV URL (e.g., https://www.instagram.com/p/SHORTCODE/)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to save the output .txt file (default: ./output)",
    )
    parser.add_argument(
        "--include-owner",
        action="store_true",
        help="Include comments authored by the post owner (default: excluded)",
    )
    parser.add_argument(
        "--no-replies",
        action="store_true",
        help="Do not traverse nested comment replies (default: replies included)",
    )
    parser.add_argument(
        "--session-user",
        default=None,
        help="Instagram username for session authentication",
    )
    parser.add_argument(
        "--session-file",
        default=None,
        help="Path to saved Instaloader session file",
    )
    parser.add_argument(
        "--session-id",
        "--sessionid",
        dest="session_id",
        default=None,
        help="Direct Instagram sessionid cookie string from your browser (recommended)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Instagram password for fresh login via mobile API (only needed once; session is saved)",
    )
    parser.add_argument(
        "--use-instaloader",
        action="store_true",
        help="Force using the old Instaloader/GraphQL provider instead of the mobile API",
    )
    return parser


def _create_provider(args: argparse.Namespace) -> CommentProvider:
    """Creates the best available CommentProvider based on CLI args."""
    session_file = Path(args.session_file) if args.session_file else None

    # If explicitly requested, use instaloader
    if args.use_instaloader:
        from src.instaloader_provider import InstaloaderCommentProvider
        return InstaloaderCommentProvider(
            session_user=args.session_user,
            session_file=session_file,
            session_id=args.session_id,
        )

    # Default: use instagrapi (mobile API) — much more reliable
    try:
        from src.instagrapi_provider import InstagrapiCommentProvider
        return InstagrapiCommentProvider(
            session_user=args.session_user,
            session_id=args.session_id,
            password=args.password,
            instaloader_session_file=session_file,
        )
    except Exception as exc:
        sys.stderr.write(f"Mobile API provider failed: {exc}\n")
        sys.stderr.write("Falling back to Instaloader/GraphQL provider...\n")
        from src.instaloader_provider import InstaloaderCommentProvider
        return InstaloaderCommentProvider(
            session_user=args.session_user,
            session_file=session_file,
            session_id=args.session_id,
        )


def main(
    argv: Sequence[str] | None = None,
    provider: CommentProvider | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        parsed_url = parse_instagram_url(args.url)
    except InvalidInstagramURLError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if provider is None:
        try:
            provider = _create_provider(args)
        except Exception as exc:
            sys.stderr.write(f"Error initializing session: {exc}\n")
            return 1

    result = extract_usernames(
        shortcode=parsed_url.shortcode,
        provider=provider,
        include_owner=args.include_owner,
        include_replies=not args.no_replies,
    )

    if result.is_partial:
        sys.stderr.write(f"Warning: {result.warning_message}\n")

    output_dir = Path(args.output_dir)
    target_path = write_usernames_file(
        shortcode=result.shortcode,
        usernames=result.usernames,
        output_dir=output_dir,
    )

    print(f"Successfully extracted {len(result.usernames)} usernames to {target_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

