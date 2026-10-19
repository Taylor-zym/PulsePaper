import argparse
import json
import logging
import sys
from typing import Any, Iterable
from urllib import error, parse, request

from config import get_text

EASYSCHOLAR_SECRET_KEY = get_text("providers", "easyscholar_secret_key", default="") or ""
EASYSCHOLAR_ENDPOINT = "https://www.easyscholar.cc/open/getPublicationRank"

DEFAULT_DISPLAY_FIELDS = ("sci", "sciBase", "sciUp", "sciUpSmall")
FIELD_LABELS = {
    "sciif": "SCI IF",
    "sci": "JCR分区",
    "sciBase": "中科院基础版",
    "sciUp": "中科院升级版",
    "sciUpSmall": "中科院升级版小类",
}

LOGGER = logging.getLogger("journal_rank")


def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def resolve_secret_key(secret_key: str | None = None) -> str | None:
    return clean_secret(secret_key) or get_text(
        "providers",
        "easyscholar_secret_key",
        default=EASYSCHOLAR_SECRET_KEY,
    )


def build_rank_url(publication_name: str, secret_key: str | None = None) -> str:
    cleaned_name = publication_name.strip()
    cleaned_key = resolve_secret_key(secret_key)
    if not cleaned_name:
        raise ValueError("publication_name cannot be empty.")
    if not cleaned_key:
        raise ValueError("secret_key cannot be empty.")
    params = {
        "secretKey": cleaned_key,
        "publicationName": cleaned_name,
    }
    return f"{EASYSCHOLAR_ENDPOINT}?{parse.urlencode(params)}"


def decode_error_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    return text or "<empty response body>"


def fetch_rank_payload(publication_name: str, secret_key: str | None = None, timeout: int = 30) -> dict[str, Any]:
    url = build_rank_url(publication_name, secret_key=secret_key)
    req = request.Request(url=url, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = decode_error_body(exc.read())
        raise RuntimeError(f"EasyScholar request failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"EasyScholar request failed: {exc.reason}") from exc

    if payload.get("code") != 200:
        raise RuntimeError(f"EasyScholar request failed: {payload.get('msg', 'unknown error')}")
    return payload


def extract_official_rank(payload: dict[str, Any], source: str = "all") -> dict[str, str]:
    rank_info = payload.get("data", {}).get("officialRank", {}).get(source, {})
    if not isinstance(rank_info, dict):
        return {}
    return rank_info


def select_rank_fields(rank_info: dict[str, str], fields: Iterable[str] = DEFAULT_DISPLAY_FIELDS) -> dict[str, str | None]:
    return {field: rank_info.get(field) for field in fields}


def get_publication_rank_summary(
    publication_name: str,
    fields: Iterable[str] = DEFAULT_DISPLAY_FIELDS,
    source: str = "all",
    secret_key: str | None = None,
    timeout: int = 30,
) -> dict[str, str | None]:
    payload = fetch_rank_payload(publication_name, secret_key=secret_key, timeout=timeout)
    rank_info = extract_official_rank(payload, source=source)
    return select_rank_fields(rank_info, fields=fields)


def print_rank_summary(publication_name: str, summary: dict[str, str | None]) -> None:
    print(f"Journal: {publication_name}")
    for field, value in summary.items():
        label = FIELD_LABELS.get(field, field)
        print(f"{label}: {value or 'N/A'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query journal rank info from EasyScholar.")
    parser.add_argument("--journal", required=True, help="Journal name, e.g. Nature.")
    parser.add_argument(
        "--fields",
        nargs="+",
        default=list(DEFAULT_DISPLAY_FIELDS),
        help="Fields to display. Default: sciBase sciUp sciUpSmall",
    )
    parser.add_argument(
        "--source",
        choices=("all", "select"),
        default="all",
        help="Which officialRank block to use. 'all' is required if you want sciUpSmall.",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--secret-key", default=EASYSCHOLAR_SECRET_KEY, help="EasyScholar secret key.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.debug)
    try:
        summary = get_publication_rank_summary(
            publication_name=args.journal,
            fields=args.fields,
            source=args.source,
            secret_key=args.secret_key,
            timeout=args.timeout,
        )
    except Exception as exc:
        LOGGER.error("%s", exc)
        return 1

    print_rank_summary(args.journal, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
