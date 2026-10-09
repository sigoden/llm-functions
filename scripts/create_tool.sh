#!/data/data/com.termux/files/usr/bin/env bash
# =============================================================================
# create_interactive_tool.sh - Interactive Tool Creation Wizard
# Generates LLM function tools in Bash, JavaScript, or Python
# Version: 3.0.0
# =============================================================================
set -eo pipefail

# @describe Interactive tool creation wizard - Generate LLM function tools in Bash, JavaScript, or Python
# @option --name!                    <STRING>  Tool name (e.g., "my_tool")
# @option --lang!                    <STRING>  Language for the tool (sh, js, py)
# @option --description              <STRING>  Tool description (e.g., "A tool to do something useful")
# @option --author                   <STRING>  Author of the tool (e.g., "Foresko Company")
# @option --version                  <STRING>  Tool version (e.g., "1.0.0")
# @option --tags                     <STRING>  Comma-separated list of tags (e.g., "utility,api")
# @option --output-dir               <STRING>  Output directory for generated tools [default: tools]
# @option --interactive              <STRING>  Enable interactive parameter collection [default: true]
# @option --overwrite                <STRING>  Overwrite existing files without asking [default: false]
# @flag   --verbose                            Enable verbose output

# =============================================================================
# GLOBALS
# =============================================================================
declare -a tool_parameters=()
declare -a tool_examples=()
TEMP_DIR=""

# =============================================================================
# CLEANUP & TRAPS
# =============================================================================


cleanup() {
    local exit_code=$?
    if [[ -n "${TEMP_DIR:-}" && -d "${TEMP_DIR:-}" ]]; then
        [[ "${argc_verbose:-}" == "true" ]] && \
            echo "[DEBUG] Cleaning up temp dir: $TEMP_DIR" >&2
        rm -rf "$TEMP_DIR"
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'echo "[ERROR] Interrupted." >&2; exit 130' INT TERM

# =============================================================================
# LOGGING
# =============================================================================

log_info() { echo "[INFO]  $*" >&2; }

log_warn() { echo "[WARN]  $*" >&2; }

log_error() { echo "[ERROR] $*" >&2; }

log_debug() {
    [[ "${argc_verbose:-}" == "true" ]] && echo "[DEBUG] $*" >&2
    return 0
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

confirm_overwrite() {
    local file="$1"
    if [[ ! -f "$file" ]]; then
        return 0
    fi
    if [[ "${argc_overwrite:-false}" == "true" ]]; then
        log_info "Overwriting existing file: $file"
        return 0
    fi
    local confirm
    read -r -p "File '$file' already exists. Overwrite? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_info "Skipped: $file"
        return 1
    fi
    return 0
}

sanitize_name() {
    local name="$1"
    echo "$name" \
        | sed 's/[^a-zA-Z0-9_-]/_/g' \
        | sed 's/^[^a-zA-Z_]*//' \
        | tr '[:upper:]' '[:lower:]'
}

to_camel_case() {
    local name="$1"
    echo "$name" \
        | sed 's/[-_]/ /g' \
        | sed 's/\b\(.\)/\u\1/g' \
        | sed 's/ //g' \
        | sed 's/^\(.\)/\l\1/'
}

to_pascal_case() {
    local name="$1"
    echo "$name" \
        | sed 's/[-_]/ /g' \
        | sed 's/\b\(.\)/\u\1/g' \
        | sed 's/ //g'
}

validate_lang() {
    local lang="$1"
    case "$lang" in
        sh|js|py) return 0 ;;
        *)
            log_error "Unsupported language: '$lang'. Supported: sh, js, py"
            return 1
            ;;
    esac
}

validate_parameters() {
    local errors=()
    declare -A seen_names

    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        if [[ -n "${seen_names[$name]:-}" ]]; then
            errors+=("Duplicate parameter name: '$name'")
        fi
        seen_names["$name"]=1

        # Validate name format
        if [[ ! "$name" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]; then
            errors+=("Invalid parameter name: '$name' (must start with letter/underscore)")
        fi

        # Validate type
        case "$type" in
            string|int|integer|float|number|boolean|literal) ;;
            *) errors+=("Unknown type '$type' for parameter '$name'") ;;
        esac
    done

    if [[ ${#tool_parameters[@]} -eq 0 ]]; then
        errors+=("At least one parameter is required")
    fi

    if [[ ${#errors[@]} -gt 0 ]]; then
        for err in "${errors[@]}"; do
            log_error "$err"
        done
        return 1
    fi
    return 0
}

# =============================================================================
# BASH TEMPLATE GENERATOR
# =============================================================================

generate_bash_template() {
    local output_dir="${argc_output_dir:-tools}"
    local file="${output_dir}/${argc_name}.sh"

    confirm_overwrite "$file" || return 1
    log_info "Generating Bash tool: $file"

    # Build argc option annotations
    local argc_options=""
    local env_options=""
    env_options+="# @env LLM_OUTPUT=/dev/stdout  Path to write LLM output"$'\n'
    env_options+="# @env LLM_OUTPUT_COLOR=1      Enable colorised output"$'\n'

    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"

        local quoted_desc flags type_annotation
        quoted_desc=$(echo "$desc" | sed 's/"/\\"/g')
        flags=""
        type_annotation=""

        [[ "$required"  == "true" ]] && flags+="!"
        [[ "$multiple"  == "true" ]] && flags+="+"

        case "$type" in
            int|integer)   type_annotation="<INT>"    ;;
            float|number)  type_annotation="<NUM>"    ;;
            boolean)       type_annotation=""         ;;
            *)             type_annotation="<STRING>" ;;
        esac

        if [[ "$type" == "boolean" ]]; then
            argc_options+="# @flag   --${name}${flags}  ${quoted_desc}"$'\n'
        else
            if [[ -n "$default_val" && "$required" != "true" ]]; then
                argc_options+="# @option --${name}${flags}  ${type_annotation}  ${quoted_desc} [default: ${default_val}]"$'\n'
            else
                argc_options+="# @option --${name}${flags}  ${type_annotation}  ${quoted_desc}"$'\n'
            fi
        fi
    done

    # Build required-parameter guard block
    local required_checks=""
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        if [[ "$required" == "true" && "$type" != "boolean" ]]; then
            local var_name
            var_name=$(echo "$name" | sed 's/-/_/g')
            required_checks+="    if [[ -z \"\${argc_${var_name}:-}\" ]]; then"$'\n'
            required_checks+="        log_error \"Required parameter --${name} is missing\""$'\n'
            required_checks+="        exit 1"$'\n'
            required_checks+="    fi"$'\n'
        fi
    done

    # Build main body – parameter logging
    local main_body=""
    main_body+="    local output_file=\"\${LLM_OUTPUT:-/dev/stdout}\""$'\n'
    main_body+="    local color_output=\"\${LLM_OUTPUT_COLOR:-0}\""$'\n'
    main_body+=$'\n'
    main_body+="    local message=\"[${argc_name}] Executing with parameters:\""$'\n'
    main_body+=$'\n'

    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        local var_name
        var_name=$(echo "$name" | sed 's/-/_/g')

        if [[ "$type" == "boolean" ]]; then
            main_body+="    [[ \"\${argc_${var_name}:-}\" == \"true\" ]] && \\"$'\n'
            main_body+="        message+=$'\\n''  --${name}: true'"$'\n'
        elif [[ "$multiple" == "true" ]]; then
            main_body+="    if [[ -n \"\${argc_${var_name}[*]:-}\" ]]; then"$'\n'
            main_body+="        message+=$'\\n''  --${name}: '\"\${argc_${var_name}[*]}\""$'\n'
            main_body+="    fi"$'\n'
        else
            main_body+="    if [[ -n \"\${argc_${var_name}:-}\" ]]; then"$'\n'
            main_body+="        message+=$'\\n''  --${name}: '\"\${argc_${var_name}}\""$'\n'
            main_body+="    fi"$'\n'
        fi
    done

    main_body+=$'\n'
    main_body+="    # Write output to LLM_OUTPUT file"$'\n'
    main_body+="    echo \"\$message\" >> \"\$output_file\""$'\n'
    main_body+=$'\n'
    main_body+="    # -------------------------------------------------------"$'\n'
    main_body+="    # TODO: Add your tool logic here"$'\n'
    main_body+="    # -------------------------------------------------------"$'\n'

    # Build examples section
    local examples_section=""
    if [[ ${#tool_examples[@]} -gt 0 ]]; then
        examples_section="# @examples"$'\n'
        for example in "${tool_examples[@]}"; do
            examples_section+="#   ${example}"$'\n'
        done
    fi

    mkdir -p "$output_dir"

    cat > "$file" <<BASH_EOF
#!/usr/bin/env bash
# =============================================================================
# ${argc_name} - ${argc_description:-Generated tool}
# Author:  ${argc_author:-Foresko Company}
# Version: ${argc_version:-1.0.0}
# Tags:    ${argc_tags:-generated}
# =============================================================================
set -eo pipefail

# describe ${argc_description:-Generated tool: ${argc_name}}
# @author   ${argc_author:-Foresko Company}
# @version  ${argc_version:-1.0.0}
# @tags     ${argc_tags:-generated}
${examples_section}
${env_options}
${argc_options}
log_info()  { echo "[INFO]  \$*" >&2; }
log_error() { echo "[ERROR] \$*" >&2; }

cleanup() {
    local exit_code=\$?
    exit "\$exit_code"
}
trap cleanup EXIT

main() {
    # --- Required parameter guards ---
${required_checks}
    # --- Parameter logging ---
${main_body}
}

eval "\$(argc --argc-eval "\$0" "\$@")"
BASH_EOF

    chmod +x "$file"
    log_info "Successfully generated: $file"
    return 0
}

# =============================================================================
# JAVASCRIPT TEMPLATE GENERATOR
# =============================================================================

generate_js_template() {
    local output_dir="${argc_output_dir:-tools}"
    local file="${output_dir}/${argc_name}.js"

    confirm_overwrite "$file" || return 1
    log_info "Generating JavaScript tool: $file"

    # Build JSDoc parameter annotations
    local jsdoc_params=""
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"

        local js_type optional
        case "$type" in
            string)        js_type="string"  ;;
            int|integer)   js_type="number"  ;;
            float|number)  js_type="number"  ;;
            boolean)       js_type="boolean" ;;
            *)             js_type="any"     ;;
        esac

        [[ "$multiple"  == "true" ]] && js_type="${js_type}[]"
        optional=""
        [[ "$required" != "true" ]] && optional="?"

        jsdoc_params+=" * @property {${js_type}}${optional} ${name} - ${desc}"$'\n'
    done

    # Build destructured function parameters with defaults
    local func_params=""
    local first=true
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"

        [[ "$first" == "false" ]] && func_params+=", "
        first=false

        if [[ "$required" != "true" && -n "$default_val" ]]; then
            local default_js
            case "$type" in
                string)  default_js="'${default_val}'" ;;
                boolean) default_js="${default_val}"    ;;
                *)       default_js="${default_val}"    ;;
            esac
            func_params+="${name} = ${default_js}"
        else
            func_params+="${name}"
        fi
    done

    # Build result object entries
    local result_entries=""
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        result_entries+="        ${name},"$'\n'
    done

    # Build required-parameter validation
    local required_js=""
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        if [[ "$required" == "true" ]]; then
            required_js+="    if (${name} === undefined || ${name} === null) {"$'\n'
            required_js+="        throw new Error('Required parameter \"${name}\" is missing');"$'\n'
            required_js+="    }"$'\n'
        fi
    done

    mkdir -p "$output_dir"

    cat > "$file" <<JS_EOF
/**
 * ${argc_description:-Generated tool: ${argc_name}}
 *
 * @author  ${argc_author:-Foresko Company}
 * @version ${argc_version:-1.0.0}
 * @tags    ${argc_tags:-generated}
 *
 * @typedef {Object} Params
${jsdoc_params} * @param {Params} params - Tool parameters
 */

'use strict';

/**
 * Execute the ${argc_name} tool.
 * @param {Object} params - Tool parameters
 * @returns {Promise<Object>} Execution result
 */
exports.run = async function ({ ${func_params} } = {}) {
    const outputFile = process.env.LLM_OUTPUT   || '/dev/stdout';
    const colorOutput = !!process.env.LLM_OUTPUT_COLOR;

    // --- Required parameter guards ---
${required_js}
    // -----------------------------------------------------------------------
    // TODO: Add your tool logic here
    // -----------------------------------------------------------------------

    const result = {
        message:   \`Executing ${argc_name}\`,
        timestamp: new Date().toISOString(),
        params: {
${result_entries}        },
    };

    return result;
};

// Support CommonJS and ES module environments
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { run: exports.run };
}
JS_EOF

    log_info "Successfully generated: $file"
    return 0
}

# =============================================================================
# PYTHON TEMPLATE GENERATOR
# =============================================================================

generate_py_template() {
    local output_dir="${argc_output_dir:-tools}"
    local file="${output_dir}/${argc_name}.py"

    confirm_overwrite "$file" || return 1
    log_info "Generating Python tool: $file"

    # Determine if Literal import is needed
    local use_literal=false
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        [[ "$type" == "literal" ]] && use_literal=true
    done

    local literal_import=""
    [[ "$use_literal" == "true" ]] && \
        literal_import="from typing import Literal"$'\n'

    # Build function signature
    local func_params="" first=true
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"

        [[ "$first" == "false" ]] && func_params+=","$'\n'"    "
        first=false

        local py_type
        case "$type" in
            string)        py_type="str"   ;;
            int|integer)   py_type="int"   ;;
            float|number)  py_type="float" ;;
            boolean)       py_type="bool"  ;;
            literal)       py_type="str"   ;;
            *)             py_type="Any"   ;;
        esac

        [[ "$multiple"  == "true"  ]] && py_type="List[${py_type}]"
        [[ "$required" != "true"   ]] && py_type="Optional[${py_type}]"

        if [[ "$required" != "true" && -n "$default_val" ]]; then
            case "$type" in
                string)  func_params+="${name}: ${py_type} = \"${default_val}\"" ;;
                boolean) func_params+="${name}: ${py_type} = ${default_val}"     ;;
                *)       func_params+="${name}: ${py_type} = ${default_val}"     ;;
            esac
        elif [[ "$required" != "true" ]]; then
            func_params+="${name}: ${py_type} = None"
        else
            func_params+="${name}: ${py_type}"
        fi
    done

    # Build docstring Args section
    local docstring_args=""
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        local req_marker=""
        [[ "$required" == "true" ]] && req_marker=" (required)"
        docstring_args+="        ${name}: ${desc}${req_marker}"$'\n'
    done

    # Build required-parameter validation
    local required_py=""
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        if [[ "$required" == "true" ]]; then
            required_py+="    if ${name} is None:"$'\n'
            required_py+="        raise ValueError(f'Required parameter \"${name}\" is missing')"$'\n'
        fi
    done

    # Build result dictionary
    local result_dict="{"$'\n'
    result_dict+="        \"message\":   f\"Executing ${argc_name}\","$'\n'
    result_dict+="        \"timestamp\": datetime.now().isoformat(),"$'\n'
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        result_dict+="        \"${name}\":     ${name},"$'\n'
    done
    result_dict+="    }"

    mkdir -p "$output_dir"

    cat > "$file" <<PY_EOF
#!/usr/bin/env python3
"""
${argc_description:-Generated tool: ${argc_name}}

Author:  ${argc_author:-Foresko Company}
Version: ${argc_version:-1.0.0}
Tags:    ${argc_tags:-generated}
"""

import os
import sys
import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
${literal_import}

def run(
    ${func_params}
) -> Dict[str, Any]:
    """
    ${argc_description:-Execute the ${argc_name} tool.}

    Args:
${docstring_args}
    Returns:
        Dict[str, Any]: Structured execution results for LLM consumption.

    Raises:
        ValueError: If a required parameter is missing.
    """
    output_file  = os.environ.get('LLM_OUTPUT',       '/dev/stdout')
    color_output = os.environ.get('LLM_OUTPUT_COLOR', '0') == '1'

    # --- Required parameter guards ---
${required_py}
    # -----------------------------------------------------------------------
    # TODO: Add your tool logic here
    # -----------------------------------------------------------------------

    result: Dict[str, Any] = ${result_dict}

    return result


if __name__ == '__main__':
    # Standalone testing – parse simple key=value args from CLI
    import argparse

    parser = argparse.ArgumentParser(description='${argc_description:-${argc_name} tool}')
PY_EOF

    # Append argparse entries for standalone testing
    for param_def in "${tool_parameters[@]}"; do
        IFS=':' read -r name type desc required default_val multiple \
            <<< "$param_def"
        local py_action=""
        [[ "$type" == "boolean" ]] && py_action=", action='store_true'"
        echo "    parser.add_argument('--${name}'${py_action}, help='${desc}')" >> "$file"
    done

    cat >> "$file" <<PY_EOF2

    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps(result, indent=2, default=str))
PY_EOF2

    chmod +x "$file"
    log_info "Successfully generated: $file"
    return 0
}

# =============================================================================
# INTERACTIVE PARAMETER COLLECTION
# =============================================================================

collect_parameters_interactive() {
    tool_parameters=()
    tool_examples=()

    echo ""
    echo "================================================================="
    echo " Parameter Collection"
    echo " Leave parameter name blank when finished."
    echo "================================================================="
    echo ""

    local param_count=0

    while true; do
        echo "--- Parameter $((param_count + 1)) ---"

        # Name
        local param_name
        read -r -p "  Name (e.g., input-file, api-key, verbose): " param_name
        if [[ -z "$param_name" ]]; then
            if (( param_count == 0 )); then
                log_warn "At least one parameter is required. Please define one."
                continue
            fi
            break
        fi
        param_name=$(sanitize_name "$param_name")

        # Type
        echo "  Type:"
        echo "    1) string   (default)"
        echo "    2) int"
        echo "    3) float"
        echo "    4) boolean  (flag – no value)"
        echo "    5) literal  (enum / fixed choices)"
        local type_choice param_type
        read -r -p "  Select type [1-5, default=1]: " type_choice
        case "${type_choice:-1}" in
            1|string)  param_type="string"  ;;
            2|int)     param_type="int"     ;;
            3|float)   param_type="float"   ;;
            4|boolean) param_type="boolean" ;;
            5|literal) param_type="literal" ;;
            *)         param_type="string"  ;;
        esac

        # Description
        local param_desc
        read -r -p "  Description: " param_desc
        param_desc="${param_desc:-A parameter for ${argc_name}}"

        # Required (mandatory with ! suffix)
        local required_choice is_required="false"
        read -r -p "  Required? [y/N]: " required_choice
        [[ "$required_choice" =~ ^[Yy]$ ]] && is_required="true"

        # Default value (skipped for required params – implements ! suffix logic)
        local default_val=""
        if [[ "$is_required" != "true" && "$param_type" != "boolean" ]]; then
            read -r -p "  Default value (leave empty for none): " default_val
        fi

        # Multiple values
        local multiple_choice is_multiple="false"
        if [[ "$param_type" != "boolean" ]]; then
            read -r -p "  Accept multiple values? [y/N]: " multiple_choice
            [[ "$multiple_choice" =~ ^[Yy]$ ]] && is_multiple="true"
        fi

        # Store
        tool_parameters+=("${param_name}:${param_type}:${param_desc}:${is_required}:${default_val}:${is_multiple}")
        param_count=$(( param_count + 1 ))

        log_debug "Added: $param_name ($param_type) required=$is_required multiple=$is_multiple default='$default_val'"
        echo ""
    done

    # Examples
    echo ""
    echo "================================================================="
    echo " Usage Examples (optional – leave blank to finish)"
    echo "================================================================="
    while true; do
        local example
        read -r -p "  Example args (e.g., '--input file.txt --output out.json'): " example
        [[ -z "$example" ]] && break
        tool_examples+=("${argc_name} ${example}")
    done

    echo ""
    validate_parameters || return 1
    log_info "Collected ${#tool_parameters[@]} parameter(s) and ${#tool_examples[@]} example(s)."
    return 0
}

# =============================================================================
# SUMMARY DISPLAY
# =============================================================================

show_summary() {
    echo ""
    echo "================================================================="
    echo " Tool Creation Summary"
    echo "================================================================="
    echo "  Name        : ${argc_name}"
    echo "  Language    : ${argc_lang}"
    echo "  Description : ${argc_description:-N/A}"
    echo "  Author      : ${argc_author:-Foresko Company}"
    echo "  Version     : ${argc_version:-1.0.0}"
    echo "  Tags        : ${argc_tags:-N/A}"
    echo "  Output Dir  : ${argc_output_dir:-tools}"
    echo "  Overwrite   : ${argc_overwrite:-false}"
    echo "  Verbose     : ${argc_verbose:-false}"
    echo "================================================================="
    echo ""
}

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

main() {
    # Apply defaults
    argc_output_dir="${argc_output_dir:-tools}"
    argc_interactive="${argc_interactive:-true}"
    argc_overwrite="${argc_overwrite:-false}"

    # Validate language early
    validate_lang "${argc_lang}" || exit 1

    # Ensure output directory exists
    mkdir -p "${argc_output_dir}"

    show_summary

    # Collect parameters interactively
    if [[ "${argc_interactive}" == "true" ]]; then
        collect_parameters_interactive || {
            log_error "Parameter collection failed or was cancelled."
            exit 1
        }
    fi

    # Resolve generator function name: generate_sh_template, etc.
    local lang_key="$argc_lang"
    [[ "$lang_key" == "sh" ]] && lang_key="bash"

    local generator="generate_${lang_key}_template"

    if declare -f "$generator" > /dev/null 2>&1; then
        if "$generator"; then
            echo ""
            log_info "Tool '${argc_name}' created successfully!"
            echo ""
            echo "  Next steps:"
            echo "    1. Edit   : ${argc_output_dir}/${argc_name}.${argc_lang}"
            echo "    2. Develop: Add your business logic inside the generated template"
            if [[ "$argc_lang" == "sh" ]]; then
                echo "    3. Test   : argc ${argc_output_dir}/${argc_name}.sh --help"
            elif [[ "$argc_lang" == "js" ]]; then
                echo "    3. Test   : node -e \"require('./${argc_output_dir}/${argc_name}.js').run({}).then(console.log)\""
            elif [[ "$argc_lang" == "py" ]]; then
                echo "    3. Test   : python3 ${argc_output_dir}/${argc_name}.py --help"
            fi
            echo ""
        else
            log_error "Failed to generate tool '${argc_name}'"
            exit 1
        fi
    else
        log_error "No generator found for language: '${argc_lang}'"
        log_error "Supported languages: sh, js, py"
        exit 1
    fi
}

# =============================================================================
# ARGC ENTRY
# =============================================================================
eval "$(argc --argc-eval "$0" "$@")"
