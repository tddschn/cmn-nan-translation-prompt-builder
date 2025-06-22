#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "jieba", "loguru", "selectolax", "pyhtml2md",
# "opencc",
# ]
# ///
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote
import jieba
import jieba.posseg as pseg
import pyhtml2md
from loguru import logger
from selectolax.lexbor import LexborHTMLParser

# --- Configuration ---
# Configure logger to output to stderr
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<level>{level: <8}</level> | <level>{message}</level>",
)
# URL template for the dictionary service
DICT_URL_TEMPLATE = "https://sutian.moe.edu.tw/zh-hant/tshiau/?lui=hua_ku&tsha={query}"
# Part-of-speech tags to drop from the query.
# 'e' = interjection (嘆詞), 'y' = modal particle (語氣詞)
POS_TO_DROP = {"e", "y"}
# Default prompt to append for the LLM
DEFAULT_LLM_PROMPT = '\n---\n### LLM INSTRUCTION\n\nBased on the original text and the provided dictionary lookups for each word, please translate the "Original Input" from 北平方言 to hokkien （需要漢字和音標）. Use the dictionary examples to ensure the translation is natural and accurate.\n'
# --- Main Logic Functions ---


def segment_text(
    text: str, mode: str, tw_to_cn_converter, cn_to_tw_converter
) -> list[str]:
    """
    Converts text to Simplified Chinese for better segmentation, performs
    segmentation, converts words back to Traditional, and filters.

    Args:
        text: The input string in Traditional Chinese.
        mode: The segmentation mode ('accurate', 'full', 'search').
        tw_to_cn_converter: OpenCC instance for TW -> CN conversion.
        cn_to_tw_converter: OpenCC instance for CN -> TW conversion.

    Returns:
        A list of unique, valid words to be queried.
    """
    logger.info(f"Using '{mode}' segmentation mode.")
    logger.info("Converting input to Simplified Chinese for jieba processing...")
    simplified_text = tw_to_cn_converter.convert(text)
    words_to_query = []
    dropped_words = []
    if mode == "accurate":
        logger.info("Performing segmentation with POS tagging...")
        words_with_flags_cn = pseg.cut(simplified_text)
        for word_cn, flag in words_with_flags_cn:
            word_cn = word_cn.strip()
            if not word_cn:
                continue
            # Convert back to Traditional Chinese for the final list
            word_tw = cn_to_tw_converter.convert(word_cn)
            if flag in POS_TO_DROP:
                dropped_words.append(f"{word_tw} ({flag})")
            else:
                words_to_query.append(word_tw)
    else:
        # Full and Search modes do not support POS tagging.
        logger.warning(
            f"POS-based filtering (for interjections) is not supported in '{mode}' mode. All parts will be queried."
        )
        if mode == "full":
            segmented_words_cn = jieba.cut(simplified_text, cut_all=True)
        elif mode == "search":
            segmented_words_cn = jieba.cut_for_search(simplified_text)
        for word_cn in segmented_words_cn:
            word_cn = word_cn.strip()
            if not word_cn:
                continue
            # Convert back to Traditional Chinese
            words_to_query.append(cn_to_tw_converter.convert(word_cn))
    # Return unique words while preserving order of first appearance
    seen = set()
    unique_filtered_words = [
        x for x in words_to_query if not (x in seen or seen.add(x))
    ]
    logger.info(
        f"Kept {len(unique_filtered_words)} unique words for lookup: {', '.join(unique_filtered_words)}"
    )
    if dropped_words:
        logger.info(
            f"Dropped {len(dropped_words)} words (interjections/particles): {', '.join(dropped_words)}"
        )
    return unique_filtered_words


def run_parallel_downloader(
    query_words: list[str], temp_dir: pathlib.Path
) -> dict[str, pathlib.Path]:
    """
    Calls the download_preserve_path_to_dir_structure.py script as a subprocess
    to download dictionary pages in parallel.

    Args:
        query_words: A list of words to look up.
        temp_dir: The temporary directory to store downloaded files.

    Returns:
        A dictionary mapping each query word to its downloaded HTML file path.
    """
    if not query_words:
        return {}
    logger.info(
        f"Preparing to download dictionary entries for {len(query_words)} words in parallel..."
    )
    urls = [DICT_URL_TEMPLATE.format(query=quote(word)) for word in query_words]
    urls_input_str = "\n".join(urls)
    downloader_script = "download_preserve_path_to_dir_structure.py"
    # Create a temporary file path for the JSON map. The file is created but closed
    # immediately so the subprocess can write to it. It will be cleaned up in the finally block.
    json_map_file = tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".json", dir=temp_dir
    )
    json_map_path = json_map_file.name
    json_map_file.close()
    logger.debug(f"Using temporary file for downloader's JSON map: {json_map_path}")
    # Command to execute. We now provide a file path to the -j argument.
    # Write JSON map to our temp file
    command = [
        downloader_script,
        "-o",
        str(temp_dir),
        "--flatten",
        "-j",
        json_map_path,
        "--no-aio",
    ]
    logger.debug(f"Running subprocess: {' '.join(command)}")
    try:
        # We pipe the URLs to the subprocess's stdin
        result = subprocess.run(
            command,
            input=urls_input_str,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        )
        if result.stderr:
            # Downloader script logs its progress to stderr, which is normal.
            logger.debug(f"Downloader stderr:\n{result.stderr.strip()}")
        # After the process completes, read the JSON map from the temp file
        with open(json_map_path, "r", encoding="utf-8") as f:
            url_to_path_map = json.load(f)
        word_to_path_map = {}
        for word in query_words:
            url = DICT_URL_TEMPLATE.format(query=quote(word))
            if url in url_to_path_map:
                word_to_path_map[word] = pathlib.Path(url_to_path_map[url])
            else:
                logger.warning(
                    f"No downloaded file path found for query word: '{word}'"
                )
        logger.info("Parallel download process completed.")
        return word_to_path_map
    except FileNotFoundError:
        logger.error(
            f"FATAL: The downloader script '{downloader_script}' was not found."
        )
        logger.error(
            "Please ensure it is in the same directory or in your system's PATH."
        )
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        logger.error("The downloader script exited with an error.")
        logger.error(f"Exit Code: {e.returncode}")
        logger.error(f"Downloader Stderr:\n{e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error(
            f"Failed to parse the JSON output from the map file: {json_map_path}"
        )
        try:
            with open(json_map_path, "r") as f_err:
                logger.error(f"Content of map file:\n{f_err.read()}")
        except Exception:
            pass  # Avoid cascading errors if file can't be read.
        sys.exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred while running the downloader: {e}")
        sys.exit(1)
    finally:
        # Clean up the temporary JSON map file
        pathlib.Path(json_map_path).unlink(missing_ok=True)


def extract_and_convert_html(
    html_path: pathlib.Path, query_word: str, is_char_lookup: bool = False
) -> tuple[bool, str]:
    """
    Parses a downloaded HTML file, extracts the relevant content, and converts it to Markdown.

    Args:
        html_path: Path to the HTML file.
        query_word: The original word or character queried.
        is_char_lookup: Flag to adjust the header level for character lookups.

    Returns:
        A tuple containing:
        - bool: True if results were found, False otherwise.
        - str: A formatted Markdown string of the dictionary results.
    """
    header_level = "#### └─" if is_char_lookup else "###"
    query_type = "字元" if is_char_lookup else "詞語"
    logger.debug(f"Extracting content for '{query_word}' from '{html_path.name}'...")
    try:
        html_content = html_path.read_text(encoding="utf-8")
        tree = LexborHTMLParser(html_content)
        # The target content is within <ol class="text-secondary">
        target_node = tree.css_first("ol.text-secondary")
        if not target_node:
            logger.warning(
                f"No dictionary results found in the HTML for '{query_word}'."
            )
            no_result_md = f"{header_level} {query_type}查詢：「{query_word}」\n\n*（無查詢結果）*\n\n---"
            return (False, no_result_md)
        # Get the HTML of the node and convert it to Markdown
        html_snippet = target_node.html
        markdown_content = pyhtml2md.convert(html_snippet).strip()
        # Clean up excessive newlines that pyhtml2md might create from list items
        markdown_content = "\n".join(
            (line.strip() for line in markdown_content.splitlines() if line.strip())
        )
        success_md = f"{header_level} {query_type}查詢：「{query_word}」\n\n{markdown_content}\n\n---"
        return (True, success_md)
    except Exception as e:
        logger.error(
            f"Failed to process file '{html_path}' for query '{query_word}': {e}"
        )
        error_md = f"{header_level} {query_type}查詢：「{query_word}」\n\n*（處理檔案時發生錯誤）*\n\n---"
        return (False, error_md)


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="A pre-processor for Beijing Dialect to Hokkien translation. Reads text, looks up words in a dictionary, and generates a Markdown document for an LLM.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "input_text",
        nargs="?",
        default=None,
        help="The input text to process. If omitted, reads from --file or stdin.",
    )
    input_group.add_argument(
        "-f",
        "--file",
        type=pathlib.Path,
        help="Path to a file containing the input text to process.",
    )
    parser.add_argument(
        "--split-mode",
        choices=["accurate", "full", "search"],
        default="accurate",
        help="The word segmentation mode to use (default: accurate).",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=DEFAULT_LLM_PROMPT,
        help="A custom prompt to append at the end of the generated Markdown file.",
    )
    args = parser.parse_args()
    # 1. Determine and read input text
    input_text = ""
    if args.input_text:
        logger.info("Reading text from command-line argument...")
        input_text = args.input_text.strip()
    elif args.file:
        logger.info(f"Reading text from file: {args.file}...")
        try:
            input_text = args.file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.error(f"Input file not found: {args.file}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error reading from file {args.file}: {e}")
            sys.exit(1)
    elif not sys.stdin.isatty():
        logger.info("Reading text from standard input...")
        input_text = sys.stdin.read().strip()
    if not input_text:
        logger.error(
            "No input text provided. Please provide text via argument, --file, or stdin."
        )
        parser.print_help()
        sys.exit(1)
    # Initialize OpenCC converters
    logger.info("Initializing OpenCC converters...")
    import opencc

    tw_to_cn_converter = opencc.OpenCC("tw2s.json")
    cn_to_tw_converter = opencc.OpenCC("s2twp.json")
    # 2. Segment text and filter words
    query_words = segment_text(
        input_text, args.split_mode, tw_to_cn_converter, cn_to_tw_converter
    )
    if not query_words:
        logger.warning("No valid words to query after segmentation. Exiting.")
        sys.exit(0)
    # Dictionary to hold the final markdown for each top-level query word
    final_word_results = {}
    with tempfile.TemporaryDirectory() as tempdir:
        temp_path = pathlib.Path(tempdir)
        logger.info(f"Created temporary directory for downloads: {temp_path}")
        # --- STAGE 1: Look up segmented words ---
        word_to_path_map_s1 = run_parallel_downloader(query_words, temp_path)
        failed_words_s1 = []
        logger.info("Processing initial word lookups...")
        for word in query_words:
            if word in word_to_path_map_s1:
                success, md_content = extract_and_convert_html(
                    word_to_path_map_s1[word], word
                )
                # Store as a list to append char results later
                final_word_results[word] = [md_content]
                if not success:
                    failed_words_s1.append(word)
            else:
                md_content = (
                    f"### 詞語查詢：「{word}」\n\n*（下載失敗或無對應檔案）*\n\n---"
                )
                final_word_results[word] = [md_content]
                failed_words_s1.append(word)
        # --- STAGE 2: Look up characters from failed words ---
        if failed_words_s1:
            logger.info(
                f"Words without results: {', '.join(failed_words_s1)}. Falling back to character-level lookup."
            )
            # Collect unique characters from failed multi-character words
            chars_to_lookup = []
            seen_chars = set()
            for word in failed_words_s1:
                if len(word) > 1:
                    for char in word:
                        if char not in seen_chars:
                            chars_to_lookup.append(char)
                            seen_chars.add(char)
            if chars_to_lookup:
                char_to_path_map_s2 = run_parallel_downloader(
                    chars_to_lookup, temp_path
                )
                char_lookup_results = {}
                logger.info("Processing character lookups...")
                for char in chars_to_lookup:
                    if char in char_to_path_map_s2:
                        _success, md_content = extract_and_convert_html(
                            char_to_path_map_s2[char], char, is_char_lookup=True
                        )
                        char_lookup_results[char] = md_content
                    else:
                        char_lookup_results[char] = (
                            f"#### └─ 字元查詢：「{char}」\n\n*（下載失敗或無對應檔案）*\n\n---"
                        )
                # Append character results to the failed word's result list
                for word in failed_words_s1:
                    if len(word) > 1:
                        for char in word:
                            if char in char_lookup_results:
                                final_word_results[word].append(
                                    char_lookup_results[char]
                                )
    # --- FINAL ASSEMBLY ---
    # Build the final Markdown from the results dictionary
    output_parts = [
        "# Translation Pre-processing Document\n",
        "## Original Input\n",
        f"> {input_text}\n",
        "---\n",
        "## Dictionary Lookup Results\n",
    ]
    logger.info("Assembling final Markdown document...")
    for word in query_words:  # Iterate in the original segmented order
        if word in final_word_results:
            # Join the main result and all sub-results (for characters)
            full_section_md = "\n".join(final_word_results[word])
            output_parts.append(full_section_md)
    # Add the final LLM prompt
    if args.prompt:
        output_parts.append(args.prompt.strip())
    # Print the final combined Markdown to stdout
    final_markdown = "\n".join(output_parts)
    print(final_markdown)
    logger.info("Successfully generated and outputted the final Markdown document.")


if __name__ == "__main__":
    # Initialize jieba. It will download the dictionary on the first run.
    logger.info("Initializing jieba...")
    jieba.initialize()
    main()
