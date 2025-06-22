# 北平方言到閩南語翻譯器 Prompt Builder (cmn-nan-translation-prompt-builder)

https://github.com/tddschn/cmn-nan-translation-prompt-builder

This is a tool for building a detailed prompt to aid a Large Language Model (LLM) in translating Mandarin Chinese (cmn, 北平方言) to Hokkien (nan, 閩南語, Bân-lâm-gí). The core function is to take a Mandarin sentence and enrich it with dictionary definitions, creating an informative context for the translation (翻譯器, huan-i̍k-khì) task.

- [北平方言到閩南語翻譯器 Prompt Builder (cmn-nan-translation-prompt-builder)](#北平方言到閩南語翻譯器-prompt-builder-cmn-nan-translation-prompt-builder)
  - [Purpose](#purpose)
  - [What It Is](#what-it-is)
  - [How It Works](#how-it-works)
  - [Requirements](#requirements)
  - [Installation](#installation)
  - [Usage](#usage)
    - [Example](#example)
    - [Example of LLM Output](#example-of-llm-output)
  - [Design \& Technical Choices](#design--technical-choices)
  - [Acknowledgements](#acknowledgements)


## Purpose

Help native / heritage speakers learn the writing of Hokkien.

Currently LLM will give 台灣優勢腔之音標, which may not be the same as the the dialect you speak, and this will likely confuse learners who are not exposed to different dialects of Hokkien.

## What It Is

This project is a command-line tool designed to pre-process a sentence from the **北平方言 (Pak-pîng dialect)** of Mandarin Chinese (ISO 639-3: **cmn**) and generate a detailed prompt for an LLM to translate it into Hokkien (ISO 639-3: **nan**).

The script automates the process of:
1.  Breaking down the input sentence into meaningful words.
2.  Looking up each word in an online Hokkien dictionary.
3.  Handling words not found in the dictionary by looking up their individual characters.
4.  Assembling all this information into a single, well-structured Markdown document.

The final Markdown output can then be passed to an LLM to perform a high-quality, context-aware translation.

## How It Works

The tool follows a multi-stage process to build the prompt:

1.  **Text Conversion & Segmentation:** The input text (in Traditional Chinese) is first converted to Simplified Chinese using `OpenCC`. This is because the `jieba` segmentation library is highly optimized for Simplified Chinese, resulting in more accurate word splits for compounds like `颱風` (typhoon) and `習慣` (habit).
2.  **Initial Dictionary Lookup:** The segmented words (converted back to Traditional Chinese) are then used to query the [教育部臺灣閩南語常用詞辭典 (Sutian)](https://sutian.moe.edu.tw/). The `download_preserve_path_to_dir_structure.py` script downloads all dictionary pages in parallel to maximize speed.
3.  **Fallback Character Lookup:** If a segmented word (e.g., "北平") does not yield a result from the dictionary, the script automatically triggers a second-stage lookup. It breaks the failed word into individual characters ("北", "平") and runs a new parallel download to fetch their definitions.
4.  **Prompt Assembly:** The script parses the downloaded HTML, converts the relevant dictionary entries to Markdown, and assembles the final document. The output is structured with the original input sentence followed by the dictionary results for each word, including any character-level fallback results nested underneath.

## Requirements

*   Python 3.11+
*   [uv](https://github.com/astral-sh/uv) (for dependency management via script headers)

## Installation

1. Clone the repository:
    ```bash
    git clone https://github.com/tddschn/cmn-nan-translation-prompt-builder
    cd cmn-nan-translation-prompt-builder
    ```

2. Move both scripts into a directory in your system's `$PATH`, such as `/usr/local/bin`:
    ```bash
    sudo mv pak_penn_to_hokkien_split_and_sutian_prompt_builder.py /usr/local/bin/
    sudo mv download_preserve_path_to_dir_structure.py /usr/local/bin/
    ```

## Usage

You can provide input text directly as a command-line argument, from a file with `-f`, or via `stdin`.

### Example

The following example uses an artificially constructed sentence to demonstrate a wide range of vocabulary.

Several [倒裝詞](http://www.shenpo.com.tw/reverse) are used in this example.

**Command:**
```bash
pak_penn_to_hokkien_split_and_sutian_prompt_builder.py '你家後面有颱風經過 今天有客人要來一起吃午飯 很熱鬧 他們已經習慣了 他們說口渴想喝芭樂汁'
```

**Partial Markdown Output (sent to `stdout`):**

<details>
  <summary>Click to expand!</summary>
  

```markdown
# Translation Pre-processing Document

## Original Input

> 你家後面有颱風經過 今天有客人要來一起吃午飯 很熱鬧 他們已經習慣了 他們說口渴想喝芭樂汁

---

## Dictionary Lookup Results

### 詞語查詢：「你家」

*(...Dictionary results for 你家...)*

---

### 詞語查詢：「後面」

*(...Dictionary results for 後面...)*

---

### 詞語查詢：「颱風」

*(...Dictionary results for 颱風...)*

---

### 詞語查詢：「習慣」

*(...Dictionary results for 習慣...)*

---

### 詞語查詢：「芭樂汁」

#### └─ 字元查詢：「芭」

*(...Dictionary results for the character 芭...)*

#### └─ 字元查詢：「樂」

*(...Dictionary results for the character 樂...)*

#### └─ 字元查詢：「汁」

*(...Dictionary results for the character 汁...)*

---
### LLM INSTRUCTION

Based on the original text and the provided dictionary lookups for each word, please translate the "Original Input" from Beijing Dialect (Mandarin) into Hokkien. Use the dictionary examples to ensure the translation is natural and accurate.
```
</details>

### Example of LLM Output

Feeding the generated Markdown prompt into a capable LLM (I'm using the free Gemini 2.5 Pro model provided by Google AI Studio) could yield a high-quality translation like this:

> **Hokkien (Hanji):** 恁兜後壁有風颱經過。今仔日有儂客欲來做伙食中晝，真鬧熱，𪜶已經習慣矣。𪜶講喙焦想欲啉菝仔汁。
>
> **Hokkien (Romanization):** Lín tau āu-piah ū hong-thai king-kuè. Kin-á-ji̍t ū lâng-kheh beh lâi tsò-hué tsia̍h-tiong-tàu, tsin lāu-jia̍t, in í-king si̍p-kuàn--ah. In kóng tshuì-ta siūnn-beh lim pá-á-tsiap.

*(Note: The above translation includes the full example sentence and was generated by a separate LLM for demonstration purposes.)*

## Design & Technical Choices

*   **Segmentation Accuracy (`OpenCC` + `Jieba`):** To achieve the most accurate word segmentation, the input text is first converted from Traditional to Simplified Chinese. This allows `jieba` to leverage its superior optimization for mainland Chinese vocabulary, correctly identifying multi-character words. The results are then converted back to Traditional for the dictionary lookup.
*   **Performance (Parallel Downloads):** Dictionary lookups are I/O-bound. To avoid a long sequential wait, a helper script `download_preserve_path_to_dir_structure.py` is used to fetch all dictionary pages in parallel, drastically reducing the total execution time.
*   **Robustness (Fallback Mechanism):** Not all words, even when correctly segmented, exist in the dictionary (e.g., new words, slang, or proper nouns). The fallback mechanism that looks up individual characters ensures that the LLM still receives some contextual information, rather than nothing at all.
*   **Parsing Speed (`selectolax` & `pyhtml2md`):** For parsing the downloaded HTML and converting it to Markdown, `selectolax` and `pyhtml2md` were chosen over more common libraries like BeautifulSoup and `markdownify` due to their significantly better performance, which is beneficial when processing many files.
*   **Dependency Management (`uv`):** The scripts use a `uv run` shebang header. This makes them self-contained by declaring their Python dependencies at the top of the file, allowing `uv` to create an ephemeral, cached virtual environment automatically. This ensures reproducibility without manual `pip install` steps.

## Acknowledgements

This project would not be possible without the excellent work of the following open-source projects and data sources:

*   **[教育部臺灣閩南語常用詞辭典 (Sutian)](https://sutian.moe.edu.tw/)**: For providing an invaluable, high-quality, and openly accessible dictionary for Hokkien.
*   **[OpenCC (Open Chinese Convert)](https://github.com/BYVoid/OpenCC)**: For the robust and accurate library that makes the crucial Traditional-Simplified-Traditional conversion workflow possible.
*   **[Jieba](https://github.com/fxsjy/jieba)**: For the powerful and fast Chinese segmentation engine that forms the core of the text processing pipeline.

<!-- ## License

This project is licensed under the MIT License. -->