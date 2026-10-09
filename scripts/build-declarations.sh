#!/data/data/com.termux/files/usr/bin/env bash

# Validation functions for tool declarations
# @describe Build tool/agent declarations from argc scripts
# @option --validate Enable pre-build validation
# @arg scriptfile The script file to process

set -e

VALIDATE="${VALIDATE:-false}"
ERRORS=()
WARNINGS=()

main() {
    local scriptfile="$1"
    
    if [[ "$VALIDATE" == "true" ]]; then
        validate_tool_declaration "$scriptfile" || return 1
    fi
    
    is_tool=false
    if [[ "$(dirname "$scriptfile")" == tools ]]; then
        is_tool=true
    fi
    if [[ "$is_tool" == "true" ]]; then
        expr='[.]' 
    else
        expr='.subcommands' 
    fi
    argc --argc-export "$scriptfile" | \
    jq "$expr" | \
    build_declarations
}

# Validate tool declaration for common errors
validate_tool_declaration() {
    local scriptfile="$1"
    local errors=()
    local warnings=()
    
    # Check file exists
    if [[ ! -f "$scriptfile" ]]; then
        errors+=("File does not exist: $scriptfile")
        report_errors "$scriptfile" errors
        return 1
    fi
    
    # Check for required @describe
    if ! grep -q "# @describe" "$scriptfile"; then
        errors+=("Missing @describe directive")
    fi
    
    # Check for empty @describe
    local describe_line
    describe_line=$(grep "# @describe" "$scriptfile" | head -1 || true)
    if [[ -n "$describe_line" ]]; then
        local description
        description=$(echo "$describe_line" | sed 's/# @describe *//')
        if [[ -z "$description" ]]; then
            errors+=("@describe directive is empty")
        fi
    fi
    
    # Check for invalid parameter names (kebab-case validation)
    while IFS= read -r line; do
        if [[ "$line" =~ ^#.*@option.*--([a-zA-Z_][a-zA-Z0-9_-]*) ]]; then
            local param_name="${BASH_REMATCH[1]}"
            if [[ "$param_name" =~ [A-Z] ]]; then
                errors+=("Parameter '$param_name' should use kebab-case (no uppercase letters)")
            fi
            if [[ "$param_name" =~ __ ]]; then
                errors+=("Parameter '$param_name' should not contain double underscores")
            fi
        fi
    done < "$scriptfile"
    
    # Check for @arg without proper naming
    while IFS= read -r line; do
        if [[ "$line" =~ ^#.*@arg\ *([a-zA-Z_][a-zA-Z0-9_-]*) ]]; then
            local arg_name="${BASH_REMATCH[1]}"
            if [[ "$arg_name" =~ [A-Z] ]]; then
                errors+=("Argument '$arg_name' should use kebab-case")
            fi
        fi
    done < "$scriptfile"
    
    # Check for potential issues with parameter descriptions
    while IFS= read -r line; do
        if [[ "$line" =~ ^#.*@option.*--[a-zA-Z_][a-zA-Z0-9_-]* ]]; then
            # Check if next line has description
            local param
            param=$(echo "$line" | sed 's/# @option *//')
            local next_line
            next_line=$(tail -n +$(grep -n "$line" "$scriptfile" | cut -d: -f1) "$scriptfile" | head -2 | tail -1)
            if [[ ! "$next_line" =~ ^#\ && ! "$next_line" =~ ^#\ @ ]]; then
                warnings+=("Parameter '$param' may be missing a description")
            fi
        fi
    done < "$scriptfile"
    
    # Report results
    if [[ ${#errors[@]} -gt 0 ]]; then
        report_errors "$scriptfile" errors
        return 1
    fi
    
    if [[ ${#warnings[@]} -gt 0 ]]; then
        report_warnings "$scriptfile" warnings
    fi
    
    return 0
}

report_errors() {
    local scriptfile="$1"
    local error_array="$2"
    echo "Validation errors in $scriptfile:" >&2
    eval "printf '  %s\n' \"\${${error_array}[@]}\"" >&2
}

report_warnings() {
    local scriptfile="$1"
    local warning_array="$2"
    echo "Validation warnings in $scriptfile:" >&2
    eval "printf '  %s\n' \"\${${warning_array}[@]}\"" >&2
}

build_declarations() {
    jq --arg is_tool "$is_tool" -r '
    def filter_declaration:
        (if $is_tool == "true" then
            .
        else
            select(.name | startswith("_") | not) 
        end) | select(.description != "");

    def parse_description(flag_option):
        if flag_option.describe == "" then
            {}
        else
            { "description": flag_option.describe }
        end;

    def parse_enum(flag_option):
        if flag_option.choice.type == "Values" then
            { "enum": flag_option.choice.data }
        else
            {}
        end;

    def parse_property(flag_option):
        [
            { condition: (flag_option.flag == true), result: { type: "boolean" } },
            { condition: (flag_option.multiple_occurs == true), result: { type: "array", items: { type: "string" } } },
            { condition: (flag_option.notations[0] == "INT"), result: { type: "integer" } },
            { condition: (flag_option.notations[0] == "NUM"), result: { type: "number" } },
            { condition: true, result: { type: "string" } } ]
        | map(select(.condition) | .result) | first 
        | (. + parse_description(flag_option))
        | (. + parse_enum(flag_option))
        ;


    def parse_parameter(flag_options):
        {
            type: "object",
            properties: (reduce flag_options[] as $item ({}; . + { ($item.id | sub("-"; "_"; "g")): parse_property($item) })),
            required: [flag_options[] | select(.required == true) | .id | sub("-"; "_"; "g")],
        };

    def parse_declaration:
        {
            name: (.name | sub("-"; "_"; "g")),
            description: .describe,
            parameters: parse_parameter([.flag_options[] | select(.id != "help" and .id != "version")])
        };
    [
        .[] | parse_declaration | filter_declaration
    ]'
}

# Generate documentation from declarations
# @describe Generate markdown documentation from declarations
# @arg declarations JSON declarations
# @arg output_dir Output directory
generate_docs() {
    local declarations="$1"
    local output_dir="$2"
    
    mkdir -p "$output_dir"
    
    # Generate markdown documentation
    {
        echo "# Tools Documentation"
        echo ""
        echo "Generated on: $(date)"
        echo ""
        echo "## Table of Contents"
        echo ""
    } > "$output_dir/tools.md"
    
    # Add table of contents
    echo "$declarations" | jq -r '.[] | "- [\(.name)](#\(.name | gsub(" "; "-") | gsub("_"; "-"))"' >> "$output_dir/tools.md"
    echo "" >> "$output_dir/tools.md"
    
    # Add detailed documentation
    echo "$declarations" | jq -r '.[] | "## \(.name)\n\n\(.description)\n\n### Parameters\n"' >> "$output_dir/tools.md"
    
    # Add parameters table
    echo "$declarations" | jq -r '.[] | .parameters.properties | to_entries[] | "- **\(.key)**: \(.value.type)\(.value.description // "" | if . != "" then " - " + . else "" end)"' >> "$output_dir/tools.md"
    
    echo "Documentation generated in $output_dir/tools.md"
}

main "$@"
