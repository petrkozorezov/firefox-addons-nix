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
  https://addons.mozilla.org/api/v5/addons/search/?lang=en-US&app=firefox&type=extension&sort=users&page_size=50&page=1
(with an optional &users__gt parameter when min-users is provided)

The API returns data in the following structure:
{
  "page_size": 50,
  "page_count": 100,
  "count": 5000,
  "next": "https://addons.mozilla.org/api/v5/addons/search/?lang=en-US&app=firefox&type=extension&sort=users&users__gt=100&page_size=50&page=2",
  "previous": null,
  "results": [...]
}

Only results with:
  status == "public" && current_version.file.status == "public"
are processed.

Each addon is mapped to the following schema:
{
  pname   = slug;
  version = current_version.version;
  url     = current_version.file.url;
  hash    = current_version.file.hash;  # Converted from a format like "sha256:..." to SRI format for NixOS.
  addonId = guid;
  meta = {
    homepage            = homepage.url.en-US;
    description         = summary.en-US;
    license             = current_version.license.slug;
    permissions         = current_version.file.permissions;
    hostPermissions     = current_version.file.host_permissions;
    optionalPermissions = current_version.file.optional_permissions;
    requiresPayment     = requires_payment;
    compatibility       = compatibility.firefox;
    categories          = categories;
    tags                = tags;
    hasEula             = has_eula;
    hasPrivacyPolicy    = has_privacy_policy;
    promotedCategory    = promoted.category;
  };
}
Non‑meta fields are required; if any is missing the script will crash.
Meta fields are optional and will only appear if present.
"""

import sys
import json
import argparse
import logging
import requests
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

def is_hex_string(s):
    """Return True if s consists solely of hexadecimal digits."""
    hex_digits = set("0123456789abcdefABCDEF")
    return all(c in hex_digits for c in s)

def convert_to_sri(hash_str, verbose, addon_guid):
    """
    Convert a hash string into SRI format for NixOS.
    If the hash is provided in the form "sha256:<hex>" (or "sha512:<hex>"),
    it extracts the hex part, converts it to base64 and returns a string like:
      "sha256-<base64>" (or "sha512-<base64>").
    If the hash already appears in SRI format or is not valid hex, returns the original.
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
    Fetch a page from the Mozilla Add-ons API.
    If min_users is None, the "users__gt" parameter is omitted.
    Any error during fetching will raise an exception.
    """
    base_url = "https://addons.mozilla.org/api/v5/addons/search/"
    params = {
        "lang": "en-US",
        "app": "firefox",
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
    Process a single addon result.
    All required fields are extracted. If any required field is missing, an exception is raised.
    Meta fields are optional and only added if present.
    """
    # Check required status values.
    if result["status"] != "public":
        raise Exception(f"Addon {result.get('guid')} does not have required status 'public'.")
    if result["current_version"]["file"]["status"] != "public":
        raise Exception(f"Addon {result.get('guid')} current_version file does not have status 'public'.")

    # Extract required fields.
    try:
        slug = result["slug"]
        if isinstance(slug, dict):
            pname = slug["en-US"]
        else:
            pname = slug
        version = result["current_version"]["version"]
        url = result["current_version"]["file"]["url"]
        hash_str = result["current_version"]["file"]["hash"]
        converted_hash = convert_to_sri(hash_str, verbose, result["guid"])
        addonId = result["guid"]
    except KeyError as e:
        raise Exception(f"Missing required field: {e}")

    mapped = {
        "pname": pname,
        "version": version,
        "url": url,
        "hash": converted_hash,
        "addonId": addonId,
    }

    # Build meta dictionary (optional fields).
    meta = {}

    # homepage: from homepage.url.en-US
    homepage_obj = result.get("homepage")
    if homepage_obj:
        url_obj = homepage_obj.get("url")
        if url_obj:
            if isinstance(url_obj, dict):
                home = url_obj.get("en-US")
            else:
                home = url_obj
            if home is not None:
                meta["homepage"] = home

    # description: from summary.en-US
    summary_obj = result.get("summary")
    if summary_obj:
        if isinstance(summary_obj, dict):
            desc = summary_obj.get("en-US")
        else:
            desc = summary_obj
        if desc is not None:
            meta["description"] = desc

    # license: from current_version.license.slug
    license_obj = result.get("current_version", {}).get("license")
    if license_obj and "slug" in license_obj:
        meta["license"] = license_obj["slug"]

    # permissions, hostPermissions, optionalPermissions from current_version.file
    file_obj = result.get("current_version", {}).get("file", {})
    if "permissions" in file_obj:
        meta["permissions"] = file_obj["permissions"]
    if "host_permissions" in file_obj:
        meta["hostPermissions"] = file_obj["host_permissions"]
    if "optional_permissions" in file_obj:
        meta["optionalPermissions"] = file_obj["optional_permissions"]

    # requiresPayment from result
    if "requires_payment" in result:
        meta["requiresPayment"] = result["requires_payment"]

    # compatibility: from compatibility.firefox
    compatibility_obj = result.get("compatibility")
    if compatibility_obj and "firefox" in compatibility_obj:
        meta["compatibility"] = compatibility_obj["firefox"]

    # categories and tags from result
    if "categories" in result:
        meta["categories"] = result["categories"]
    if "tags" in result:
        meta["tags"] = result["tags"]

    # hasEula and hasPrivacyPolicy from result
    if "has_eula" in result:
        meta["hasEula"] = result["has_eula"]
    if "has_privacy_policy" in result:
        meta["hasPrivacyPolicy"] = result["has_privacy_policy"]

    # promotedCategory: from promoted.category
    promoted_obj = result.get("promoted")
    if promoted_obj and "category" in promoted_obj:
        meta["promotedCategory"] = promoted_obj["category"]

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
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                            format="%(levelname)s: %(message)s")

    # Fetch page 1 synchronously.
    first_page = fetch_page(1, args.page_size, args.min_users, args.verbose)
    total_pages = first_page.get("page_count", 1)
    if args.verbose:
        logging.debug("API reports %d pages available.", total_pages)

    # Determine how many pages to fetch.
    if args.pages is not None:
        requested_pages = min(args.pages, total_pages)
    else:
        requested_pages = total_pages

    if args.verbose:
        logging.debug("Fetching %d pages in total.", requested_pages)

    results_list = []
    # Process results from page 1.
    for addon in first_page.get("results", []):
        mapped = process_result(addon, args.verbose)
        results_list.append(mapped)

    # If more pages are needed, fetch them concurrently.
    if requested_pages > 1:
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            future_to_page = {
                executor.submit(fetch_page, page, args.page_size, args.min_users, args.verbose): page
                for page in range(2, requested_pages + 1)
            }
            for future in as_completed(future_to_page):
                data = future.result()  # raises exception if fetch_page fails
                for addon in data.get("results", []):
                    mapped = process_result(addon, args.verbose)
                    results_list.append(mapped)

    # Sort the results by the 'pname' field.
    sorted_results = sorted(results_list, key=lambda x: x["pname"])
    json.dump(sorted_results, sys.stdout, indent=2)
    sys.stdout.flush()

if __name__ == "__main__":
    main()
