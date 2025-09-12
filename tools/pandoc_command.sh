#!/usr/bin/env bash
set -e

# @describe Convert documents between formats using pandoc.

# @option --from![bibtex|biblatex|bits|commonmark|commonmark_x|creole|csljson|csv|tsv|djot|docbook|docx|dokuwiki|endnotexml|epub|fb2|gfm|haddock|html|ipynb|jats|jira|json|latex|markdown|markdown_mmd|markdown_phpextra|markdown_strict|mediawiki|man|mdoc|muse|native|odt|opml|org|pod|ris|rtf|rst|t2t|textile|tikiwiki|twiki|typst|vimwiki|xml] <FORMAT>   Input format (e.g. markdown, html, docx, pdf)
# @option --to![ansi|asciidoc|asciidoc_legacy|asciidoctor|beamer|bibtex|biblatex|chunkedhtml|commonmark|commonmark_x|context|csljson|djot|docbook|docbook5|docx|dokuwiki|epub|epub2|fb2|gfm|haddock|html|html4|icml|ipynb|jats_archiving|jats_articleauthoring|jats_publishing|jats|jira|json|latex|man|markdown|markdown_mmd|markdown_phpextra|markdown_strict|markua|mediawiki|ms|muse|native|odt|opml|opendocument|org|pdf|plain|pptx|rst|rtf|texinfo|textile|slideous|slidy|dzslides|revealjs|s5|tei|typst|xml|xwiki|zimwiki] <FORMAT>     Output format (e.g. markdown, html, docx, pdf)
# @option --inputs+    Array of files used input (optional, if omitted use --contents)
# @option --contents   Raw input text instead of file
# @option --output     Output file path (optional, default: stdout)

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
  "$ROOT_DIR/utils/guard_operation.sh"
  input_arg=()
  output_arg=()

  if [[ -n "$argc_input" ]]; then
    input_arg+=("$argc_input")
  elif [[ -n "$argc_contents" ]]; then
    input_arg+=(-)
  else
    echo "Error: Either --input or --contents must be provided." >&2
    exit 1
  fi

  if [[ -n "$argc_output" ]]; then
    output_arg+=(-o "$argc_output")
  else
    output_arg+=(-)
  fi

  if [[ -n "$argc_contents" ]]; then
    printf "%s" "$argc_contents" | pandoc -f "$argc_from" -t "$argc_to" "${output_arg[@]}" >>"$LLM_OUTPUT"
  else
    pandoc -f "$argc_from" -t "$argc_to" "${input_arg[@]}" "${output_arg[@]}" >>"$LLM_OUTPUT"
  fi
}

eval "$(argc --argc-eval "$0" "$@")"
