#!/usr/bin/env bash
set -e

# @describe Perform a web search using DuckDuckGo API to get up-to-date information or additional context.
# Use this when you need current information or feel a search could provide a better answer.

# @env SEARCH_MAX_RESULTS=5 The max results to return.
# @option --query! The query to search for.

main() {
    encoded_query="$(jq -nr --arg q "$argc_query" '$q|@uri')"
    vqd="$(curl -fsSL -X POST https://duckduckgo.com -d "q=$encoded_query" | sed -En 's/.*vqd=([0-9-]+)&.*/\1/p')"
    url="https://links.duckduckgo.com/d.js?q=$encoded_query&kl=wt-wt&l=wt-wt&p=&s=0&df=&vqd=$vqd&bing_market=wt-WT&ex=-1"
    data="$(curl -fsSL "$url" | sed -En 's/.*DDG.pageLayout.load\(\x27d\x27,\[(.*)\]\);DDG.duckbar.load\(.*/\1/p')"
    echo "[$data]" | jq '
def strip_tags:
  gsub("<[^>]*>"; "");

def unescape_html_entities:
  gsub("&amp;"; "&") |
  gsub("&lt;"; "<") |
  gsub("&gt;"; ">") |
  gsub("&quot;"; "\"") | 
  gsub("&apos;"; "'\''") |
  gsub("&#x27;"; "'\''") |
  gsub("&nbsp;"; " ");

def normalize: strip_tags | unescape_html_entities;

[.[:'"$SEARCH_MAX_RESULTS"'] | .[] | select(has("u")) | {link: .u, title: (.t | normalize), snippet: (.a | normalize)}]
' >> "$LLM_OUTPUT"

}

eval "$(argc --argc-eval "$0" "$@")"
