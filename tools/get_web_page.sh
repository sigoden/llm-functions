#!/usr/bin/env bash
set -e

# @describe Fetches the HTML content from a specified webpage URL and converts it to Markdown format.
# Use it to answer user questions that require up-to-date content from web pages.
# @meta require-tools curl,pandoc,sed
# @option --url! The URL to scrape.

main() {
  # span and div tags are dropped from the HTML https://pandoc.org/MANUAL.html#raw-htmltex and sed removes any inline SVG images in image tags from the Markdown content.
  curl -fsSL "$argc_url" | pandoc -f html-native_divs-native_spans -t gfm-raw_html | sed -E 's/!\[.*?\]\((data:image\/svg\+xml[^)]+)\)//g'
}

eval "$(argc --argc-eval "$0" "$@")"
