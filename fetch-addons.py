#!/usr/bin/env nix-shell
#!nix-shell -i python -p python3 python3Packages.requests
"""
This script fetches Firefox extension data from the Mozilla Add-ons API.
It accepts optional command-line arguments:
  - --pages: number of pages to fetch. If not provided, all pages are fetched.
  - --min-users: minimum number of users. If not provided, no users__gt parameter is added.
  - --verbose: enable verbose debug output (to stderr)
  - --parallel: number of parallel processes (default: 4)
  - --page-size: number of results per page (default: 50)

The API endpoint used is:
  https://addons.mozilla.org/api/v5/addons/search/?lang=en-US&type=extension&sort=users&page_size=50&page=1
(with an optional &users__gt parameter when min-users is provided)

The API returns data in the following structure:
{
  "page_size": 50,
  "page_count": 100,
  "count": 5000,
  "next": "https://addons.mozilla.org/api/v5/addons/search/?lang=en-US&type=extensions&sort=users&users__gt=100&page_size=50&page=2",
  "previous": null,
  "results": [...]
}

Only results where:
  status == "public" && current_version.file.status == "public"
are processed.

Each addon is mapped to the following schema:
{
  pname   = slug;                # if slug is a dict, its "en-US" field is used
  version = current_version.version;
  url     = current_version.file.url;
  hash    = current_version.file.hash;   # Converted to SRI format (sha256-base64) for NixOS if applicable
  addonId = guid;
  meta = {
    homepage       = homepage.url.en-US;
    description    = summary.en-US;
    license        = current_version.license.slug;
    permissions    = current_version.file.permissions;
    requiresPayment= requires_payment;
    compatibility  = compatibility.firefox;
    categories     = categories;
    tags           = tags;
  };
}
Note: The meta fields are optional.
"""

import sys
import json
import argparse
import logging
import requests
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

def is_hex_string(s):
    """
    Check if string s consists solely of hexadecimal digits.
    """
    hex_digits = set("0123456789abcdefABCDEF")
    return all(c in hex_digits for c in s)

def convert_to_sri(hash_str, verbose, addon_guid):
    """
    Convert a hash string into SRI format for NixOS.
    If the hash is provided in the form "sha256:<hex>" (or "sha512:<hex>"),
    it converts the hexadecimal part to base64 and returns a string in the form:
      "sha256-<base64>" (or "sha512-<base64>").
    If the hash already appears in SRI format (i.e. starts with "sha256-" or "sha512-"),
    or if the hash is not valid hex, the original hash is returned.
    """
    if hash_str.startswith("sha256-") or hash_str.startswith("sha512-"):
        if verbose:
            logging.debug("Addon %s: hash already in SRI format: %s", addon_guid, hash_str)
        return hash_str

    if hash_str.startswith("sha256:"):
        algo = "sha256"
        hex_part = hash_str[len("sha256:"):]
    elif hash_str.startswith("sha512:"):
        algo = "sha512"
        hex_part = hash_str[len("sha512:"):]
    else:
        if is_hex_string(hash_str):
            algo = "sha256"
            hex_part = hash_str
        else:
            if verbose:
                logging.debug("Addon %s: hash is not a valid hex string, returning original: %s", addon_guid, hash_str)
            return hash_str

    if not is_hex_string(hex_part):
        if verbose:
            logging.debug("Addon %s: hash part is not valid hex, returning original: %s", addon_guid, hash_str)
        return hash_str

    hash_bytes = bytes.fromhex(hex_part)
    sri_hash = f"{algo}-" + base64.b64encode(hash_bytes).decode("utf-8")
    return sri_hash

def fetch_page(page, page_size, min_users, verbose):
    """
    Fetch a page from the Mozilla Add-ons API using the provided parameters.
    If min_users is None, the "users__gt" parameter is omitted.
    Any error during fetching will raise an exception.
    """
    base_url = "https://addons.mozilla.org/api/v5/addons/search/"
    params = {
        "lang": "en-US",
        "type": "extension",
        "sort": "users",
        "page_size": page_size,
        "page": page
    }
    if min_users is not None:
        params["users__gt"] = min_users

    if verbose:
        logging.debug("Fetching page %d with parameters: %s", page, params)
    response = requests.get(base_url, params=params)
    response.raise_for_status()
    data = response.json()
    if verbose:
        logging.debug("Page %d fetched successfully with %d results", page, len(data.get("results", [])))
    return data

def process_result(result, verbose):
    """
    Process an individual addon result.
    Only addons with:
      - result['status'] == "public"
      - result['current_version']['file']['status'] == "public"
    are processed.
    The result is then mapped to the required schema.
    """
    if result.get("status") != "public":
        if verbose:
            logging.debug("Skipping addon %s due to non-public status", result.get("guid"))
        return None

    current_version = result.get("current_version", {})
    file_info = current_version.get("file", {})

    if file_info.get("status") != "public":
        if verbose:
            logging.debug("Skipping addon %s due to non-public file status in current_version", result.get("guid"))
        return None

    mapped = {}
    slug = result.get("slug")
    if isinstance(slug, dict):
        mapped["pname"] = slug.get("en-US")
    else:
        mapped["pname"] = slug

    mapped["version"] = current_version.get("version")
    mapped["url"] = file_info.get("url")

    orig_hash = file_info.get("hash")
    if orig_hash:
        mapped["hash"] = convert_to_sri(orig_hash, verbose, result.get("guid"))
    else:
        mapped["hash"] = None

    mapped["addonId"] = result.get("guid")

    meta = {}
    homepage = result.get("homepage", {})
    if homepage:
        url_obj = homepage.get("url")
        if isinstance(url_obj, dict):
            homepage_url = url_obj.get("en-US")
        else:
            homepage_url = url_obj
        if homepage_url:
            meta["homepage"] = homepage_url

    summary = result.get("summary")
    if summary:
        if isinstance(summary, dict):
            desc = summary.get("en-US")
        else:
            desc = summary
        if desc:
            meta["description"] = desc

    license_info = current_version.get("license", {})
    if license_info:
        license_slug = license_info.get("slug")
        if license_slug:
            meta["license"] = license_slug

    permissions = file_info.get("permissions")
    if permissions is not None:
        meta["permissions"] = permissions

    requires_payment = result.get("requires_payment")
    if requires_payment is not None:
        meta["requiresPayment"] = requires_payment

    compatibility = result.get("compatibility", {})
    if compatibility:
        firefox_compat = compatibility.get("firefox")
        if firefox_compat:
            meta["compatibility"] = firefox_compat

    categories = result.get("categories")
    if categories is not None:
        meta["categories"] = categories

    tags = result.get("tags")
    if tags is not None:
        meta["tags"] = tags

    if meta:
        mapped["meta"] = meta

    return mapped

def main():
    parser = argparse.ArgumentParser(
        description="Fetch Firefox extension data via the Mozilla Add-ons API."
    )
    parser.add_argument("--pages", type=int,
                        help="Number of pages to fetch. If not provided, all pages are fetched.")
    parser.add_argument("--min-users", type=int, default=None,
                        help="Minimum number of users. If not provided, no users__gt parameter is added.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug output")
    parser.add_argument("--parallel", type=int, default=4, help="Number of parallel processes (default: 4)")
    parser.add_argument("--page-size", type=int, default=50, help="Number of results per page (default: 50)")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s")

    # Fetch page 1 synchronously
    first_page = fetch_page(1, args.page_size, args.min_users, args.verbose)
    total_pages = first_page.get("page_count", 1)
    if args.verbose:
        logging.debug("API reports %d pages available.", total_pages)

    # Determine number of pages to fetch.
    if args.pages is not None:
        requested_pages = min(args.pages, total_pages)
    else:
        requested_pages = total_pages

    if args.verbose:
        logging.debug("Fetching %d pages in total.", requested_pages)

    results_list = []
    # Process results from page 1
    for addon in first_page.get("results", []):
        mapped = process_result(addon, args.verbose)
        if mapped is not None:
            results_list.append(mapped)

    # If more pages are needed, fetch them concurrently.
    if requested_pages > 1:
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            future_to_page = {
                executor.submit(fetch_page, page, args.page_size, args.min_users, args.verbose): page
                for page in range(2, requested_pages + 1)
            }
            for future in as_completed(future_to_page):
                page_number = future_to_page[future]
                data = future.result()
                for addon in data.get("results", []):
                    mapped = process_result(addon, args.verbose)
                    if mapped is not None:
                        results_list.append(mapped)

    # Sort the results by the 'pname' field
    sorted_results = sorted(results_list, key=lambda x: x.get("pname", ""))

    json.dump(sorted_results, sys.stdout, indent=2)
    sys.stdout.flush()

if __name__ == "__main__":
    main()
