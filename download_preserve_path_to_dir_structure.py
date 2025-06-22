#!/usr/bin/env uv run
# /// script
# requires-python = '>=3.11'
# dependencies = [
#     "requests", "aiohttp"
# ]
# ///
import argparse
import sys
import os
from pathlib import Path
from urllib.parse import urlparse
import requests
import logging
import asyncio
import aiohttp
import mimetypes
import re
from typing import Tuple, List, Optional, Set  # Added for MIME type guessing

# --- Configuration ---
# Chunk size for downloading files
DOWNLOAD_CHUNK_SIZE = 8192
# User-Agent for requests
# use chrome on windows
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)
# --- Logging Setup ---
from urllib.parse import unquote


def slugify(text: str) -> str:
    """
    Convert a string to a slug.
    Lowercases, removes non-word characters (alphanumerics and underscores are kept),
    replaces spaces and multiple hyphens with a single hyphen.
    Strips leading/trailing hyphens.
    """
    if not text:
        return ""
    # Replace non-alphanumeric (excluding underscore and dot) with hyphen.
    # Keeping dots can be useful for segments like "v1.0" but may need care.
    # For this specific slugification of path segments combined, replacing dots is safer.
    text = re.sub("[^\\w\\s-]", "-", str(text).lower())  # Ensure text is string
    # Replace whitespace and multiple hyphens with a single hyphen
    text = re.sub("[-\\s]+", "-", text).strip("-")
    return text


def build_save_path(
    url: str, save_dir_base: Path, args: argparse.Namespace
) -> Tuple[Optional[Path], List[str]]:  # Return type changed
    # add_suffix_value: str | None, # This parameter is removed, args.add_suffix is used directly
    '\n    Constructs the local save path based on the URL, base directory, strip prefix,\n    flattening options, and suffix policy from args. URL-decodes path components.\n\n    Args:\n        url: The URL string to process.\n        save_dir_base: The base directory Path object where files will be saved.\n        args: The parsed command-line arguments (argparse.Namespace).\n              Expected to contain strip_url_prefix, flatten options, and add_suffix.\n              args.add_suffix can be:\n                - None: Auto-detect extension later using Content-Type. Filename based on URL.\n                - "": Force no suffix (unless part of URL filename). \'index\' for dir-like.\n                - ".ext": Apply this suffix based on existing rules (if URL is dir-like, or has no extension).\n\n    Returns:\n        A tuple containing: \n          - A Path object representing the full local save path, or None if the path\n            cannot be determined.\n          - A list of decoded source directory segments from the URL, potentially used for deconfliction.\n'
    try:
        parsed_url = urlparse(url)
    except ValueError:
        log.error(f"Invalid URL format: {url}")
        return (None, [])
    if not parsed_url.scheme or not parsed_url.netloc:
        log.error(f"URL missing scheme or network location: {url}")
        return (None, [])
    original_url_path_str = parsed_url.path
    strip_prefix_val = args.strip_url_prefix
    path_part_for_structure_encoded = original_url_path_str
    matched_prefix = False
    if strip_prefix_val:
        try:
            parsed_strip_prefix_obj = urlparse(strip_prefix_val)
            full_prefix_path = parsed_strip_prefix_obj.path
            if (
                parsed_url.scheme == parsed_strip_prefix_obj.scheme
                and parsed_url.netloc == parsed_strip_prefix_obj.netloc
            ):
                current_url_path_encoded = parsed_url.path
                normalized_current_path = current_url_path_encoded
                normalized_prefix_path = full_prefix_path
                if normalized_current_path.startswith(normalized_prefix_path):
                    matched_prefix = True
                    path_part_for_structure_encoded = current_url_path_encoded[
                        len(normalized_prefix_path) :
                    ]
                elif (
                    len(normalized_prefix_path) == len(normalized_current_path) + 1
                    and normalized_prefix_path.endswith("/")
                    and normalized_prefix_path.startswith(normalized_current_path)
                ):
                    matched_prefix = True
                    path_part_for_structure_encoded = ""
                elif (
                    len(normalized_current_path) == len(normalized_prefix_path) + 1
                    and normalized_current_path.endswith("/")
                    and normalized_current_path.startswith(normalized_prefix_path)
                ):
                    matched_prefix = True
                    path_part_for_structure_encoded = ""
                if matched_prefix:
                    log.debug(
                        f"Stripped prefix '{full_prefix_path}', relative encoded path for structure is '{path_part_for_structure_encoded}'"
                    )
                else:
                    log.warning(
                        f"URL path '{current_url_path_encoded}' does not start with prefix path '{full_prefix_path}'. Using full encoded path from domain for structure."
                    )
            else:
                log.warning(
                    f"URL '{url}' does not match prefix scheme/netloc '{strip_prefix_val}'. Using full encoded path from domain for structure."
                )
        except ValueError:
            log.error(f"Invalid strip-prefix format: {strip_prefix_val}")
    else:
        matched_prefix = True  # No prefix to match, so conceptually it's fine
    path_part_for_structure_encoded = path_part_for_structure_encoded.lstrip("/")
    encoded_segments_from_path = [
        comp for comp in path_part_for_structure_encoded.split("/") if comp
    ]
    decoded_segments_from_path = [unquote(s) for s in encoded_segments_from_path]
    # Determine prospective_filename and source_dir_segments (these are crucial for deconfliction)
    prospective_filename_from_url = ""
    source_dir_segments_for_deconflict: List[str] = []  # This will be returned
    is_dir_like_url_path = (
        original_url_path_str.endswith("/") or not decoded_segments_from_path
    )
    if is_dir_like_url_path:
        # prospective_filename_from_url remains "" which implies "index" later
        source_dir_segments_for_deconflict = decoded_segments_from_path
    elif decoded_segments_from_path:
        prospective_filename_from_url = decoded_segments_from_path[-1]
        source_dir_segments_for_deconflict = decoded_segments_from_path[:-1]
    else:  # e.g. http://example.com (no path) interpreted as file-like if not ending in /
        # but parsed_url.path would be "", so decoded_segments_from_path is empty.
        # This case leads to "index" anyway.
        source_dir_segments_for_deconflict = []
    actual_filename = ""
    add_suffix_policy = args.add_suffix
    if add_suffix_policy is not None:
        if add_suffix_policy == "":
            if is_dir_like_url_path or not prospective_filename_from_url:
                actual_filename = "index"
            else:
                actual_filename = os.path.splitext(prospective_filename_from_url)[0]
        else:
            forced_suffix = add_suffix_policy
            if is_dir_like_url_path:
                actual_filename = "index" + forced_suffix
            elif not prospective_filename_from_url:
                actual_filename = "index" + forced_suffix
            elif "." not in prospective_filename_from_url:
                actual_filename = prospective_filename_from_url + forced_suffix
            else:
                actual_filename = prospective_filename_from_url
    elif is_dir_like_url_path:
        actual_filename = "index"
    elif not prospective_filename_from_url:
        actual_filename = "index"
    else:
        actual_filename = prospective_filename_from_url
    if not actual_filename and (
        is_dir_like_url_path or not prospective_filename_from_url
    ):
        actual_filename = "index"
    if args.preserve_query_params and parsed_url.query:
        # Slugify the query string to make it safe for filenames
        query_slug = slugify(parsed_url.query)
        # Split filename into stem and extension to insert the query slug
        stem, ext = os.path.splitext(actual_filename)
        actual_filename = f"{stem}_query_{query_slug}{ext}"
        log.debug(f"Preserving query params, new filename part is: {actual_filename}")
    if not actual_filename:
        log.error(
            f"Could not determine filename for URL: {url}. Path processing resulted in empty filename. Skipping."
        )
        return (None, source_dir_segments_for_deconflict)  # or []
    final_dir_components = []
    if args.flatten or (
        args.flatten_to_nth_path == 0 and args.flatten_to_nth_path is not None
    ):
        pass  # final_dir_components remains empty, files go into save_dir_base
    elif args.flatten_to_domain:
        final_dir_components = [parsed_url.netloc]
    elif args.flatten_to_nth_path is not None:
        num_levels = args.flatten_to_nth_path
        # source_dir_segments_for_deconflict are the URL path segments for hierarchy
        final_dir_components = source_dir_segments_for_deconflict[:num_levels]
    else:  # Default hierarchical structure
        if not matched_prefix and strip_prefix_val:
            final_dir_components.append(parsed_url.netloc)
        elif not strip_prefix_val:
            final_dir_components.append(parsed_url.netloc)
        final_dir_components.extend(source_dir_segments_for_deconflict)
    all_path_construct_components = final_dir_components + [actual_filename]
    if not all_path_construct_components or not all_path_construct_components[-1]:
        log.error(
            f"Path construction resulted in invalid components for {url}: {all_path_construct_components}. Skipping."
        )
        return (None, source_dir_segments_for_deconflict)  # or []
    full_save_path = save_dir_base.joinpath(*all_path_construct_components)
    log.debug(f"Calculated initial save path: {full_save_path} for URL {url}")
    return (full_save_path, source_dir_segments_for_deconflict)


def main_sync(args, urls_to_process, save_dir_base):
    """Main sequential execution path using requests."""
    import uuid

    log.info("Using requests for sequential downloads.")
    url_path_mappings = {}
    success_count = 0
    fail_count = 0
    skip_count = 0
    occupied_final_save_paths_in_run: Set[Path] = set()
    for url in urls_to_process:
        log.debug(f"Processing URL sync: {url}")
        initial_save_path, source_url_dir_segments = build_save_path(
            url, save_dir_base, args
        )
        if not initial_save_path:
            log.warning(f"Skipping URL due to path construction issue: {url}")
            fail_count += 1
            continue
        if args.skip_existing and initial_save_path.exists():
            if initial_save_path.is_dir():
                log.error(
                    f"Skipping '{url}': Target path '{initial_save_path}' exists but is a directory. Cannot treat as existing file."
                )
                fail_count += 1
                continue
            log.info(
                f"Skipping '{url}': Target file '{initial_save_path}' (initial path) already exists."
            )
            skip_count += 1
            url_path_mappings[url] = str(initial_save_path.resolve())
            continue
        current_save_path = initial_save_path
        is_flattening_active = (
            args.flatten
            or (args.flatten_to_nth_path == 0 and args.flatten_to_nth_path is not None)
            or args.flatten_to_domain
            or (args.flatten_to_nth_path is not None and args.flatten_to_nth_path > 0)
        )
        if is_flattening_active:
            temp_path_for_deconflict = initial_save_path
            num_parents_used_for_deconflict = 0
            max_parents_available = len(source_url_dir_segments)
            deconflict_failed_for_url = False  # Flag for this URL
            while (
                temp_path_for_deconflict.exists()
                or temp_path_for_deconflict in occupied_final_save_paths_in_run
            ):
                if num_parents_used_for_deconflict < max_parents_available:
                    num_parents_used_for_deconflict += 1
                    base_name_part_for_slugging = initial_save_path.stem
                    parent_segments_for_slugging = source_url_dir_segments[
                        max_parents_available
                        - num_parents_used_for_deconflict : max_parents_available
                    ]
                    all_parts_for_slugging = parent_segments_for_slugging + [
                        base_name_part_for_slugging
                    ]
                    string_to_slugify = "/".join(
                        (p for p in all_parts_for_slugging if p)
                    )
                    new_stem = slugify(string_to_slugify)
                    temp_path_for_deconflict = initial_save_path.parent / (
                        new_stem + initial_save_path.suffix
                    )
                else:
                    stem_for_fallback = temp_path_for_deconflict.stem
                    if args.deconflict_random_suffix:
                        found_name = False
                        for _ in range(100):
                            random_suffix = uuid.uuid4().hex[:6]
                            fallback_stem = f"{stem_for_fallback}_{random_suffix}"
                            candidate_fallback_path = initial_save_path.parent / (
                                fallback_stem + initial_save_path.suffix
                            )
                            if (
                                not candidate_fallback_path.exists()
                                and candidate_fallback_path
                                not in occupied_final_save_paths_in_run
                            ):
                                temp_path_for_deconflict = candidate_fallback_path
                                log.warning(
                                    f"Deconflicted '{url}' by adding random suffix: '{temp_path_for_deconflict.name}'"
                                )
                                found_name = True
                                break
                        if not found_name:
                            log.error(
                                f"Failed to deconflict filename for {url} at '{initial_save_path.parent}' even with random suffixes. Skipping."
                            )
                            deconflict_failed_for_url = True
                    else:
                        fallback_counter = 1
                        while True:
                            fallback_stem = f"{stem_for_fallback}_{fallback_counter}"
                            candidate_fallback_path = initial_save_path.parent / (
                                fallback_stem + initial_save_path.suffix
                            )
                            if (
                                not candidate_fallback_path.exists()
                                and candidate_fallback_path
                                not in occupied_final_save_paths_in_run
                            ):
                                temp_path_for_deconflict = candidate_fallback_path
                                log.warning(
                                    f"Deconflicted '{url}' by adding numeric suffix: '{temp_path_for_deconflict.name}'"
                                )
                                break
                            fallback_counter += 1
                            if fallback_counter > 100:
                                log.error(
                                    f"Failed to deconflict filename for {url} at '{initial_save_path.parent}' even with numeric fallback. Skipping."
                                )
                                deconflict_failed_for_url = True
                                break
                    break  # Break from parent-adding while loop
            if deconflict_failed_for_url:
                fail_count += 1
                continue  # Skip to next URL
            if current_save_path != temp_path_for_deconflict:
                log.info(
                    f"Original path '{initial_save_path.name}' for '{url}' conflicted or existed. Using deconflicted name '{temp_path_for_deconflict.name}' in '{temp_path_for_deconflict.parent}'."
                )
                current_save_path = temp_path_for_deconflict
        occupied_final_save_paths_in_run.add(current_save_path)
        if current_save_path.is_dir():
            log.error(
                f"Target path '{current_save_path}' exists and is a directory. Cannot overwrite. URL: {url}. Skipping."
            )
            fail_count += 1
            continue
        if current_save_path.parent.exists() and (
            not current_save_path.parent.is_dir()
        ):
            log.error(
                f"Cannot create file '{current_save_path}'. Parent path '{current_save_path.parent}' exists but is not a directory. URL: {url}. Skipping."
            )
            fail_count += 1
            continue
        downloaded_final_path = download_file(url, current_save_path, args)
        if downloaded_final_path:
            success_count += 1
            url_path_mappings[url] = str(downloaded_final_path.resolve())
        else:
            fail_count += 1
    log.info("--------------------")
    log.info(f"Sync Download summary:")
    log.info(f"  Successfully downloaded: {success_count}")
    log.info(f"  Skipped (already exist): {skip_count}")
    log.info(f"  Failed/Skipped (other):  {fail_count}")
    log.info("--------------------")
    return (url_path_mappings, success_count, fail_count, skip_count)


async def main_async(args, urls_to_process, save_dir_base):
    log.info("Using aiohttp for parallel downloads.")
    occupied_final_save_paths_in_run: Set[Path] = set()
    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
    url_path_mappings = {}
    success_count = 0
    fail_count = 0
    skip_count = 0
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for url in urls_to_process:
            tasks.append(
                process_url_async(
                    url, save_dir_base, args, session, occupied_final_save_paths_in_run
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            # This implies an error within process_url_async itself or asyncio task management
            # process_url_async is designed to catch its own exceptions and return a "fail" status
            log.error(f"A download task unexpectedly raised an exception: {item}")
            fail_count += 1  # Or perhaps needs a more specific URL if available
        else:
            # item should be (status, original_url, final_path_str_or_none)
            status, original_url, final_path_str = item
            if status == "success":
                success_count += 1
                if final_path_str:
                    url_path_mappings[original_url] = final_path_str
                else:  # Should not happen for success
                    log.error(
                        f"Success reported for {original_url} but no path returned."
                    )
                    fail_count += 1
            elif status == "skip":
                skip_count += 1
                if final_path_str:  # Skipped items have a known path
                    url_path_mappings[original_url] = final_path_str
            elif status == "fail":
                fail_count += 1
            else:  # Should not happen
                log.error(
                    f"Unknown status '{status}' for URL {original_url}. Treating as failure."
                )
                fail_count += 1
    log.info("--------------------")
    log.info(f"Async Download summary:")
    log.info(f"  Successfully downloaded: {success_count}")
    log.info(f"  Skipped (already exist): {skip_count}")
    log.info(f"  Failed/Skipped (other):  {fail_count}")
    log.info("--------------------")
    # The decision to exit is handled by main() based on fail_count
    return (url_path_mappings, success_count, fail_count, skip_count)


async def process_url_async(
    url: str,
    save_dir_base: Path,
    args: argparse.Namespace,
    session: aiohttp.ClientSession,
    occupied_final_save_paths_in_run: Set[Path],
) -> Tuple[str, str, Optional[str]]:
    """
    Processes a single URL asynchronously.
    Returns a tuple: (status_string, original_url, final_resolved_path_string_or_None).
    Status can be "success", "skip", "fail".
    """
    import uuid

    log.debug(f"Processing URL async: {url}")
    initial_save_path, source_url_dir_segments = build_save_path(
        url, save_dir_base, args
    )
    if not initial_save_path:
        log.warning(f"Skipping URL due to path construction issue: {url}")
        return ("fail", url, None)
    if args.skip_existing and initial_save_path.exists():
        # Ensure the existing path is not a directory if we are expecting a file
        if initial_save_path.is_dir():
            log.error(
                f"Skipping '{url}': Target path '{initial_save_path}' exists but is a directory. Cannot treat as existing file."
            )
            return ("fail", url, None)
        log.info(
            f"Skipping '{url}': Target file '{initial_save_path}' (initial path) already exists."
        )
        return ("skip", url, str(initial_save_path.resolve()))
    current_save_path = initial_save_path
    is_flattening_active = (
        args.flatten
        or (args.flatten_to_nth_path == 0 and args.flatten_to_nth_path is not None)
        or args.flatten_to_domain
        or (args.flatten_to_nth_path is not None and args.flatten_to_nth_path > 0)
    )
    if is_flattening_active:
        temp_path_for_deconflict = initial_save_path
        num_parents_used_for_deconflict = 0
        max_parents_available = len(source_url_dir_segments)
        while (
            temp_path_for_deconflict.exists()
            or temp_path_for_deconflict in occupied_final_save_paths_in_run
        ):
            if num_parents_used_for_deconflict < max_parents_available:
                num_parents_used_for_deconflict += 1
                base_name_part_for_slugging = initial_save_path.stem
                parent_segments_for_slugging = source_url_dir_segments[
                    max_parents_available
                    - num_parents_used_for_deconflict : max_parents_available
                ]
                all_parts_for_slugging = parent_segments_for_slugging + [
                    base_name_part_for_slugging
                ]
                string_to_slugify = "/".join((p for p in all_parts_for_slugging if p))
                new_stem = slugify(string_to_slugify)
                temp_path_for_deconflict = initial_save_path.parent / (
                    new_stem + initial_save_path.suffix
                )
            else:
                stem_for_fallback = temp_path_for_deconflict.stem
                if args.deconflict_random_suffix:
                    found_name = False
                    for _ in range(100):  # Try 100 times
                        random_suffix = uuid.uuid4().hex[:6]
                        fallback_stem = f"{stem_for_fallback}_{random_suffix}"
                        candidate_fallback_path = initial_save_path.parent / (
                            fallback_stem + initial_save_path.suffix
                        )
                        if (
                            not candidate_fallback_path.exists()
                            and candidate_fallback_path
                            not in occupied_final_save_paths_in_run
                        ):
                            temp_path_for_deconflict = candidate_fallback_path
                            log.warning(
                                f"Deconflicted '{url}' by adding random suffix: '{temp_path_for_deconflict.name}'"
                            )
                            found_name = True
                            break
                    if not found_name:
                        log.error(
                            f"Failed to deconflict filename for {url} at '{initial_save_path.parent}' even with random suffixes. Skipping."
                        )
                        return ("fail", url, None)
                else:
                    fallback_counter = 1
                    while True:
                        fallback_stem = f"{stem_for_fallback}_{fallback_counter}"
                        candidate_fallback_path = initial_save_path.parent / (
                            fallback_stem + initial_save_path.suffix
                        )
                        if (
                            not candidate_fallback_path.exists()
                            and candidate_fallback_path
                            not in occupied_final_save_paths_in_run
                        ):
                            temp_path_for_deconflict = candidate_fallback_path
                            log.warning(
                                f"Deconflicted '{url}' by adding numeric suffix: '{temp_path_for_deconflict.name}'"
                            )
                            break
                        fallback_counter += 1
                        if fallback_counter > 100:
                            log.error(
                                f"Failed to deconflict filename for {url} at '{initial_save_path.parent}' even with numeric fallback. Skipping."
                            )
                            return ("fail", url, None)
                break
        if current_save_path != temp_path_for_deconflict:
            log.info(
                f"Original path '{current_save_path.name}' for '{url}' conflicted or existed. Using deconflicted name '{temp_path_for_deconflict.name}' in '{temp_path_for_deconflict.parent}'."
            )
            current_save_path = temp_path_for_deconflict
    occupied_final_save_paths_in_run.add(current_save_path)
    if current_save_path.is_dir():
        log.error(
            f"Target path '{current_save_path}' exists and is a directory. Cannot overwrite. URL: {url}. Skipping."
        )
        return ("fail", url, None)
    if current_save_path.parent.exists() and (not current_save_path.parent.is_dir()):
        log.error(
            f"Cannot create file '{current_save_path}'. Parent path '{current_save_path.parent}' exists but is not a directory. URL: {url}. Skipping."
        )
        return ("fail", url, None)
    downloaded_final_path = await download_file_aio(
        session, url, current_save_path, args
    )
    if downloaded_final_path:
        return ("success", url, str(downloaded_final_path.resolve()))
    else:
        return ("fail", url, None)


async def download_file_aio(
    session: aiohttp.ClientSession, url: str, save_path: Path, args: argparse.Namespace
) -> Optional[Path]:
    """
    Downloads a file from a URL using aiohttp and saves it to the specified path.
    If args.add_suffix is None (auto mode), may attempt to rename the file based
    on Content-Type after download.

    Args:
        session: The aiohttp ClientSession to use.
        url: The URL string to download from.
        save_path: The local Path object where the file should be initially saved.
        args: The parsed command-line arguments, used for add_suffix policy.

    Returns:
        The final Path object of the downloaded file if successful, None otherwise.
    """
    log.info(f"Attempting async download:")
    log.info(f"  From: {url}")
    log.info(f"  To:   {save_path} (initial path)")  # Log initial path
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error(f"Failed to create directory {save_path.parent}: {e}")
        return None
    headers = {
        "User-Agent": USER_AGENT
    }  # Path used for final logging and potential rename target
    final_save_path = save_path
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            response.raise_for_status()
            with open(save_path, "wb") as f:
                while True:
                    chunk = await response.content.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
            # Post-download processing for suffix if in auto mode
            if args.add_suffix is None:  # Auto-suffix mode
                content_type = response.headers.get("Content-Type")
                if content_type:
                    current_name = save_path.name
                    current_stem, current_ext = os.path.splitext(current_name)
                    mime_type = content_type.split(";")[0].strip().lower()
                    if mime_type and mime_type != "application/octet-stream":
                        guessed_extension = mimetypes.guess_extension(mime_type)
                        if guessed_extension and guessed_extension != current_ext:
                            if (
                                current_stem == "index"
                                and (not current_ext)
                                or (not current_ext and current_stem == current_name)
                            ):
                                new_name = current_stem + guessed_extension
                                new_save_path_candidate = save_path.with_name(new_name)
                                try:
                                    if new_save_path_candidate.exists():
                                        log.warning(
                                            f"Cannot rename {save_path} to {new_save_path_candidate} based on Content-Type: target already exists."
                                        )
                                    else:
                                        save_path.rename(new_save_path_candidate)
                                        log.info(
                                            f"Renamed '{save_path.name}' to '{new_name}' based on Content-Type '{mime_type}'."
                                        )
                                        final_save_path = new_save_path_candidate
                                except OSError as e_rename:
                                    log.error(
                                        f"Failed to rename {save_path} to {new_save_path_candidate}: {e_rename}"
                                    )
            log.info(f"Successfully downloaded {url} to {final_save_path}")
            return final_save_path
    except aiohttp.ClientError as e:
        log.error(f"Async download failed for {url}: {e}")
        if save_path.exists() and (not save_path.is_dir()):
            try:
                save_path.unlink()
                log.debug(f"Removed partially downloaded file: {save_path}")
            except OSError as unlink_e:
                log.error(f"Could not remove partial file {save_path}: {unlink_e}")
        return None
    except asyncio.TimeoutError:
        log.error(f"Async download timed out for {url}")
        if save_path.exists() and (not save_path.is_dir()):
            try:
                save_path.unlink()
                log.debug(f"Removed partially downloaded file on timeout: {save_path}")
            except OSError as unlink_e:
                log.error(f"Could not remove partial file {save_path}: {unlink_e}")
        return None
    except IOError as e:
        log.error(f"Failed to write file {save_path}: {e}")
        return None
    except Exception as e:
        log.error(f"An unexpected error occurred during async download of {url}: {e}")
        if save_path.exists() and (not save_path.is_dir()):  # Generic cleanup
            try:
                save_path.unlink()
            except OSError:
                pass
        return None


def download_file(
    url: str, save_path: Path, args: argparse.Namespace
) -> Optional[Path]:
    """
    Downloads a file from a URL using requests and saves it to the specified path.
    If args.add_suffix is None (auto mode), may attempt to rename the file based
    on Content-Type after download.

    Args:
        url: The URL string to download from.
        save_path: The local Path object where the file should be initially saved.
        args: The parsed command-line arguments, used for add_suffix policy.

    Returns:
        The final Path object of the downloaded file if successful, None otherwise.
    """
    log.info(f"Attempting sequential download:")
    log.info(f"  From: {url}")
    log.info(f"  To:   {save_path} (initial path)")  # Log initial path
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error(f"Failed to create directory {save_path.parent}: {e}")
        return None
    headers = {
        "User-Agent": USER_AGENT
    }  # Path used for final logging and potential rename target
    final_save_path = save_path
    try:
        with requests.get(url, stream=True, headers=headers, timeout=30) as response:
            response.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)
            # Post-download processing for suffix if in auto mode
            if args.add_suffix is None:  # Auto-suffix mode
                content_type = response.headers.get("Content-Type")
                if content_type:
                    current_name = save_path.name
                    current_stem, current_ext = os.path.splitext(current_name)
                    mime_type = content_type.split(";")[0].strip().lower()
                    if mime_type and mime_type != "application/octet-stream":
                        guessed_extension = mimetypes.guess_extension(mime_type)
                        if guessed_extension and guessed_extension != current_ext:
                            if (
                                current_stem == "index"
                                and (not current_ext)
                                or (not current_ext and current_stem == current_name)
                            ):
                                new_name = current_stem + guessed_extension
                                new_save_path_candidate = save_path.with_name(new_name)
                                try:
                                    if new_save_path_candidate.exists():
                                        log.warning(
                                            f"Cannot rename {save_path} to {new_save_path_candidate} based on Content-Type: target already exists."
                                        )
                                    else:
                                        save_path.rename(new_save_path_candidate)
                                        log.info(
                                            f"Renamed '{save_path.name}' to '{new_name}' based on Content-Type '{mime_type}'."
                                        )
                                        final_save_path = new_save_path_candidate
                                except OSError as e_rename:
                                    log.error(
                                        f"Failed to rename {save_path} to {new_save_path_candidate}: {e_rename}"
                                    )
            log.info(f"Successfully downloaded {url} to {final_save_path}")
            return final_save_path
    except requests.exceptions.RequestException as e:
        log.error(f"Download failed for {url}: {e}")
        if save_path.exists() and (not save_path.is_dir()):
            try:
                save_path.unlink()
                log.debug(f"Removed partially downloaded file: {save_path}")
            except OSError as unlink_e:
                log.error(f"Could not remove partial file {save_path}: {unlink_e}")
        return None
    except IOError as e:
        log.error(f"Failed to write file {save_path}: {e}")
        return None
    except Exception as e:
        log.error(f"An unexpected error occurred during download of {url}: {e}")
        if save_path.exists() and (not save_path.is_dir()):
            try:
                save_path.unlink()
            except OSError:
                pass
        return None


# --- Main Execution ---


def main():
    parser = argparse.ArgumentParser(
        description="Download files from URLs, preserving path structure or flattening output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n\n[...epilog unchanged...]\n\n5. Download directory index files, auto-detecting extension (e.g., .html from Content-Type):\n   echo "https://example.com/docs/" | %(prog)s -o web_docs\n\n6. Download file, auto-detecting extension (e.g., .jpeg from Content-Type if URL is \'server/image\'):\n   echo "https://example.com/about" | %(prog)s -o web_docs\n\n   To force \'.html\' (old default behavior for these cases):\n   echo "https://example.com/docs/" | %(prog)s -o web_docs --add-suffix .html\n\n   To force no suffix for \'server/image\' (becomes \'image\'):\n   echo "https://example.com/image" | %(prog)s -o web_docs --add-suffix ""\n\n[...rest of epilog examples might need minor review for --add-suffix context...]',
    )
    parser.add_argument(
        "urls",
        metavar="URL",
        nargs="*",
        help="One or more URLs to download. If none provided here or via -I, reads from stdin if available.",
    )
    parser.add_argument(
        "-I",
        "--file",
        metavar="FILE",
        nargs="+",
        help='Read URLs from one or more FILEs, one URL per line. Use "-" to read from stdin explicitly.',
    )
    parser.add_argument(
        "-o",
        "--save-dir",
        metavar="DIR",
        default=".",
        help="Base directory to save downloaded files (default: current directory).",
    )
    parser.add_argument(
        "-P",
        "--strip-url-prefix",
        metavar="PREFIX_URL",
        default=None,
        help="URL prefix to strip when creating the local directory structure. Must match scheme, domain, and beginning of the path.",
    )
    parser.add_argument(
        "--add-suffix",
        metavar="SUFFIX",
        default=None,
        help="Suffix policy. If not provided (default), attempts to use Content-Type to determine extension for files that would otherwise have no extension (e.g. URL paths ending in '/' become 'index.type', '/file' becomes 'file.type'). 'application/octet-stream' does not get an extension. If URL provides an extension (e.g. 'file.txt'), it's used. Set to an empty string ('') to generally prevent adding suffixes (e.g. '/path/' -> 'index', '/file' -> 'file'). Set to a specific suffix (e.g. '.html') to apply it to filenames from dir-like URLs or URLs without extensions (e.g. '/path/' -> 'index.html', '/file' -> 'file.html'). A non-empty suffix does not override existing extensions from URLs.",
    )
    parser.add_argument(
        "-S",
        "--skip-existing",
        action="store_true",
        help="If the target file path (as determined by URL structure and --add-suffix, *before* potential Content-Type based rename) already exists, skip downloading this URL.",
    )
    parser.add_argument(
        "--preserve-query-params",
        action="store_true",
        help="Preserve URL query parameters in the filename by slugifying and appending them.",
    )
    parser.add_argument(
        "--deconflict-random-suffix",
        action="store_true",
        help="When a filename conflict occurs and path-based deconfliction is not possible, resolve it by adding a short random suffix instead of a numeric one (e.g., 'file_a1b2c3.txt').",
    )
    parser.add_argument(
        "--no-aio",
        action="store_true",
        help="Disable parallel downloads (aiohttp) and use sequential downloads (requests).",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose debug logging."
    )
    flatten_group = parser.add_mutually_exclusive_group()
    flatten_group.add_argument(
        "-f",
        "--flatten",
        action="store_true",
        help="Flatten output: store all files directly in the save directory (e.g., save_dir/file.txt). Overrides default path preservation.",
    )
    flatten_group.add_argument(
        "-F",
        "--flatten-to-domain",
        action="store_true",
        help="Flatten output to domain level: store files in save_dir/domain_name/file.txt. Overrides default path preservation.",
    )
    flatten_group.add_argument(
        "--flatten-to-nth-path",
        type=int,
        metavar="LEVEL",
        default=None,
        help="Flatten output to Nth path component level from the URL's path (after any prefix stripping): store files in save_dir/path_comp1/.../path_compN/file.txt. LEVEL=0 is equivalent to --flatten (stores in save_dir directly). LEVEL=1 means save_dir/path_comp1/file.txt, etc. Overrides default path preservation.",
    )
    parser.add_argument(
        "-j",
        "--save-url-to-path-map-json",
        nargs="?",
        const="stdout",
        default=None,
        metavar="FILE_PATH",
        help="Save a JSON map of URLs to their final local save paths (including for skipped existing files). If FILE_PATH is not provided, output to stdout.",
    )
    args = parser.parse_args()
    if args.verbose:
        log.setLevel(logging.DEBUG)
        log.debug("Verbose logging enabled.")
    if args.add_suffix is not None:
        if args.add_suffix == "":
            pass
        elif not args.add_suffix.startswith("."):
            log.warning(
                f"Suffix '{args.add_suffix}' does not start with '.'; prepending '.'"
            )
            args.add_suffix = "." + args.add_suffix
    urls_to_process = []
    read_from_stdin_explicitly = False
    if args.file:
        for file_arg in args.file:
            if file_arg == "-":
                if read_from_stdin_explicitly:
                    log.warning(
                        "Reading from stdin specified multiple times ('-I -'). Reading only once."
                    )
                    continue
                log.info("Reading URLs from standard input (via -I -)...")
                read_from_stdin_explicitly = True
                try:
                    for line in sys.stdin:
                        url = line.strip()
                        if url and (not url.startswith("#")):
                            urls_to_process.append(url)
                except KeyboardInterrupt:
                    log.info("\nInterrupted reading from stdin.")
            else:
                file_path = Path(file_arg)
                log.info(f"Reading URLs from file: {file_path}")
                if not file_path.is_file():
                    log.error(f"File not found: {file_arg}. Skipping this file.")
                    continue
                try:
                    with open(file_path, "r") as f:
                        for line in f:
                            url = line.strip()
                            if url and (not url.startswith("#")):
                                urls_to_process.append(url)
                except IOError as e:
                    log.error(
                        f"Error reading file {file_arg}: {e}. Skipping this file."
                    )
                except Exception as e:
                    log.error(
                        f"An unexpected error occurred reading file {file_arg}: {e}. Skipping this file."
                    )
    urls_to_process.extend(args.urls)
    if (
        not urls_to_process
        and (not read_from_stdin_explicitly)
        and (not sys.stdin.isatty())
    ):
        log.info(
            "No URLs provided via arguments or file, reading implicitly from standard input..."
        )
        try:
            for line in sys.stdin:
                url = line.strip()
                if url and (not url.startswith("#")):
                    urls_to_process.append(url)
        except KeyboardInterrupt:
            log.info("\nInterrupted reading from stdin.")
    if not urls_to_process:
        log.error("No URLs provided or found.")
        parser.print_help()
        sys.exit(1)
    log.info(f"Processing {len(urls_to_process)} URL(s).")
    log.info(f"Base save directory: {Path(args.save_dir).resolve()}")
    if args.strip_url_prefix:
        log.info(f"Stripping URL prefix: {args.strip_url_prefix}")
    if args.add_suffix is None:
        log.info(
            "Suffix policy: Auto-detect using Content-Type or URL. No default forced suffix (like .html)."
        )
    elif args.add_suffix == "":
        log.info(
            'Suffix policy: Explicitly disabled (--add-suffix ""). Filenames will generally not have added extensions.'
        )
    else:
        log.info(
            f"Suffix policy: Attempt to add/use '{args.add_suffix}' for URLs without extensions or dir-like paths (existing URL extensions preserved)."
        )
    if args.skip_existing:
        log.info("Will skip download if target file (initial path) already exists.")
    if args.flatten:
        log.info(
            "Flattening: All files will be saved directly into the output directory."
        )
    elif args.flatten_to_domain:
        log.info("Flattening: Files will be saved under output_dir/domain_name/.")
    elif args.flatten_to_nth_path is not None:
        if args.flatten_to_nth_path == 0:
            log.info(
                "Flattening: --flatten-to-nth-path 0 is equivalent to --flatten. All files in output_dir."
            )
        else:
            log.info(
                f"Flattening: Files will be saved under output_dir/up_to_{args.flatten_to_nth_path}_path_components/."
            )
    else:
        log.info(
            "Path preservation: Default hierarchical directory structure will be used."
        )
    save_dir_base = Path(args.save_dir)
    try:
        save_dir_base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error(f"Failed to create base save directory {save_dir_base}: {e}")
        sys.exit(1)
    url_path_mappings = {}
    # success_count, fail_count, skip_count will be returned by sync/async main funcs
    if args.no_aio:
        url_path_mappings, _, fail_count, _ = main_sync(
            args, urls_to_process, save_dir_base
        )
    else:
        try:
            url_path_mappings, _, fail_count, _ = asyncio.run(
                main_async(args, urls_to_process, save_dir_base)
            )
        except KeyboardInterrupt:
            log.info("\nDownload process interrupted by user.")
            sys.exit(1)  # fail_count will be 0 here, but process is aborted.
        except Exception as e:  # Catch other potential asyncio.run errors
            log.error(f"An error occurred during asynchronous execution: {e}")
            fail_count = len(
                urls_to_process
            )  # Assume all failed if asyncio itself blew up
    if args.save_url_to_path_map_json is not None:
        import json  # Import locally as it's only used here.

        # Sort by URL for consistent output, then convert to dict
        # The map already comes as a dict, sort items for output
        output_map_sorted_items = sorted(url_path_mappings.items())
        output_map_dict = dict(output_map_sorted_items)
        if args.save_url_to_path_map_json == "stdout":
            log.info("Outputting URL-to-Path map to stdout.")
            try:
                json.dump(output_map_dict, sys.stdout, indent=2)
                sys.stdout.write("\n")  # For cleaner terminal output
            except Exception as e:
                log.error(f"Failed to write URL-to-Path map to stdout: {e}")
        else:
            output_file_path = Path(args.save_url_to_path_map_json)
            log.info(f"Saving URL-to-Path map to JSON file: {output_file_path}")
            try:
                output_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file_path, "w") as f:
                    json.dump(output_map_dict, f, indent=2)
            except IOError as e:
                log.error(f"Failed to write URL-to-Path map to {output_file_path}: {e}")
            except Exception as e:  # Catch other errors during file writing
                log.error(
                    f"An unexpected error occurred while writing URL-to-Path map to {output_file_path}: {e}"
                )
    if fail_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
