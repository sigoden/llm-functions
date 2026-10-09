#!/usr/bin/awk -f

# Apply a diff file to an original target
# Usage: awk -f patch.awk target-file patch-file1 [patch-file2 ...]
#   or: awk -f patch.awk multi-file.patch

function resolveFilename(line,    resolved) {
    resolved = line
    sub(/^(---|\+\+\+)[ \t]+/, "", resolved)
    sub(/\t.*$/, "", resolved)
    sub(/\r$/, "", resolved)

    if (resolved == "/dev/null") {
        return ""
    }
    if (resolved ~ /^[ab]\//) {
        resolved = substr(resolved, 3)
    }
    return resolved
}

function parsePatchFile(patchFile,    line, status, mode, currentFile, resolved, parts, oldPart, oldSubParts, newPart, newSubParts, firstChar, sanitizedLine) {
    mode = "none"
    currentFile = ""

    while ((status = (getline line < patchFile)) > 0) {
        sub(/\r$/, "", line)

        if (substr(line, 1, 4) == "--- ") {
            resolved = resolveFilename(line)
            if (resolved != "") currentFile = resolved
            continue
        }

        if (substr(line, 1, 4) == "+++ ") {
            resolved = resolveFilename(line)
            if (resolved != "") currentFile = resolved
            continue
        }

        if (substr(line, 1, 3) == "@@ ") {
            mode = "hunk"
            totalHunks++
            hunkFile[totalHunks] = currentFile

            split(line, parts, " ")
            oldPart = substr(parts[2], 2)
            split(oldPart, oldSubParts, ",")
            hunkOldStart[totalHunks] = oldSubParts[1] + 0
            hunkOldLength[totalHunks] = (oldSubParts[2] == "" ? 1 : oldSubParts[2] + 0)

            newPart = substr(parts[3], 2)
            split(newPart, newSubParts, ",")
            hunkNewStart[totalHunks] = newSubParts[1] + 0
            hunkNewLength[totalHunks] = (newSubParts[2] == "" ? 1 : newSubParts[2] + 0)

            hunkTotalOriginalLines[totalHunks] = 0
            hunkTotalUpdatedLines[totalHunks] = 0
            continue
        }

        if (mode == "hunk") {
            firstChar = substr(line, 1, 1)
            if (firstChar == "-" || firstChar == "+" || firstChar == " ") {
                sanitizedLine = substr(line, 2)
                if (firstChar != "+") {
                    hunkTotalOriginalLines[totalHunks]++
                    hunkOriginalLines[totalHunks, hunkTotalOriginalLines[totalHunks]] = sanitizedLine
                }
                if (firstChar != "-") {
                    hunkTotalUpdatedLines[totalHunks]++
                    hunkUpdatedLines[totalHunks, hunkTotalUpdatedLines[totalHunks]] = sanitizedLine
                }
            } else if (line == "") {
                sanitizedLine = ""
                hunkTotalOriginalLines[totalHunks]++
                hunkOriginalLines[totalHunks, hunkTotalOriginalLines[totalHunks]] = sanitizedLine
                hunkTotalUpdatedLines[totalHunks]++
                hunkUpdatedLines[totalHunks, hunkTotalUpdatedLines[totalHunks]] = sanitizedLine
            } else {
                mode = "none"
            }
        }
    }
    close(patchFile)
    if (status < 0) {
        printf "error: cannot read patch file %s\n", patchFile > "/dev/stderr"
        exit 1
    }
}

function readFile(filename,   line, count, status) {
    count = 0
    delete lines
    while ((status = (getline line < filename)) > 0) {
        sub(/\r$/, "", line)
        count++
        lines[count] = line
    }
    close(filename)
    if (status < 0) {
        return -1
    }
    return count
}

function writeFile(filename, count, i) {
    close(filename)
    for (i = 1; i <= count; i++) {
        print lines[i] > filename
    }
    close(filename)
}

function matchHunk(hunkIdx, startIdx,    origCount, i) {
    origCount = hunkTotalOriginalLines[hunkIdx]
    if (startIdx + origCount - 1 > totalLines) {
        return 0
    }
    if (origCount == 0) {
        return 1
    }
    for (i = 1; i <= origCount; i++) {
        if (lines[startIdx + i - 1] != hunkOriginalLines[hunkIdx, i]) {
            return 0
        }
    }
    return 1
}

function replaceLines(startIdx, originalCount, updatedArray, updatedCount,    shift, i) {
    shift = updatedCount - originalCount

    if (shift > 0) {
        for (i = totalLines; i >= startIdx + originalCount; i--) {
            lines[i + shift] = lines[i]
        }
    } else if (shift < 0) {
        for (i = startIdx + originalCount; i <= totalLines; i++) {
            lines[i + shift] = lines[i]
        }
        for (i = totalLines + shift + 1; i <= totalLines; i++) {
            delete lines[i]
        }
    }

    for (i = 1; i <= updatedCount; i++) {
        lines[startIdx + i - 1] = updatedArray[i]
    }

    totalLines += shift
    return shift
}

BEGIN {
    if (ARGC < 2) {
        print "Usage: awk -f patch.awk target-file patch-file1 [patch-file2 ...]" > "/dev/stderr"
        print "   or: awk -f patch.awk multi-file.patch" > "/dev/stderr"
        exit 1
    }

    totalHunks = 0

    if (ARGC == 2) {
        patchFile = ARGV[1]
        parsePatchFile(patchFile)

        if (totalHunks == 0) {
            print "error: no patch hunks found" > "/dev/stderr"
            exit 1
        }

        delete files
        filesCount = 0
        for (i = 1; i <= totalHunks; i++) {
            file = hunkFile[i]
            if (file == "") continue
            if (!(file in files)) {
                files[file] = 1
                filesList[++filesCount] = file
            }
        }

        if (filesCount == 0) {
            print "error: no target files specified in patch" > "/dev/stderr"
            exit 1
        }

        for (f = 1; f <= filesCount; f++) {
            file = filesList[f]
            totalLines = readFile(file)
            if (totalLines < 0) {
                totalLines = 0
                delete lines
            }

            lineOffset = 0
            for (hunkIdx = 1; hunkIdx <= totalHunks; hunkIdx++) {
                if (hunkFile[hunkIdx] != file) continue

                matchIndex = 0
                expectedLine = hunkOldStart[hunkIdx] + lineOffset

                if (expectedLine >= 1 && expectedLine <= totalLines + 1) {
                    if (matchHunk(hunkIdx, expectedLine)) {
                        matchIndex = expectedLine
                    }
                }

                if (matchIndex == 0) {
                    for (i = 1; i <= totalLines + 1; i++) {
                        if (matchHunk(hunkIdx, i)) {
                            matchIndex = i
                            break
                        }
                    }
                }

                if (matchIndex == 0) {
                    printf "error: patch hunk %d failed to apply to %s\n", hunkIdx, file > "/dev/stderr"
                    exit 1
                }

                delete tempUpdated
                updatedCount = hunkTotalUpdatedLines[hunkIdx]
                for (i = 1; i <= updatedCount; i++) {
                    tempUpdated[i] = hunkUpdatedLines[hunkIdx, i]
                }

                originalCount = hunkTotalOriginalLines[hunkIdx]
                shift = replaceLines(matchIndex, originalCount, tempUpdated, updatedCount)
                lineOffset += (matchIndex - expectedLine) + shift
            }

            writeFile(file, totalLines)
            printf "Successfully applied patch to %s\n", file > "/dev/stderr"
        }

    } else {
        targetFile = ARGV[1]
        totalLines = readFile(targetFile)
        if (totalLines < 0) {
            printf "error: cannot read target file %s\n", targetFile > "/dev/stderr"
            exit 1
        }

        for (p = 2; p < ARGC; p++) {
            patchFile = ARGV[p]

            totalHunks = 0
            delete hunkFile
            delete hunkOldStart
            delete hunkOldLength
            delete hunkNewStart
            delete hunkNewLength
            delete hunkTotalOriginalLines
            delete hunkOriginalLines
            delete hunkTotalUpdatedLines
            delete hunkUpdatedLines

            parsePatchFile(patchFile)

            if (totalHunks == 0) {
                printf "error: no patch hunks found in %s\n", patchFile > "/dev/stderr"
                exit 1
            }

            lineOffset = 0
            for (hunkIdx = 1; hunkIdx <= totalHunks; hunkIdx++) {
                matchIndex = 0
                expectedLine = hunkOldStart[hunkIdx] + lineOffset

                if (expectedLine >= 1 && expectedLine <= totalLines + 1) {
                    if (matchHunk(hunkIdx, expectedLine)) {
                        matchIndex = expectedLine
                    }
                }

                if (matchIndex == 0) {
                    for (i = 1; i <= totalLines + 1; i++) {
                        if (matchHunk(hunkIdx, i)) {
                            matchIndex = i
                            break
                        }
                    }
                }

                if (matchIndex == 0) {
                    printf "error: patch hunk %d from %s failed to apply\n", hunkIdx, patchFile > "/dev/stderr"
                    exit 1
                }

                delete tempUpdated
                updatedCount = hunkTotalUpdatedLines[hunkIdx]
                for (i = 1; i <= updatedCount; i++) {
                    tempUpdated[i] = hunkUpdatedLines[hunkIdx, i]
                }

                originalCount = hunkTotalOriginalLines[hunkIdx]
                shift = replaceLines(matchIndex, originalCount, tempUpdated, updatedCount)
                lineOffset += (matchIndex - expectedLine) + shift
            }
        }

        for (i = 1; i <= totalLines; i++) {
            print lines[i]
        }
    }
    exit 0
}
#!/data/data/com.termux/files/usr/bin/env bash
# @describe Apply a unified diff patch to a file or directory
# @option --path! <PATH>        Path to the file or directory to patch
# @option --contents! <TEXT>    Unified diff contents to apply
# @flag --dry-run               Preview changes without writing them
# @flag --backup                Create .bak files before modifying
# @flag --verbose               Enable verbose colorized output
# @flag --json-output           Output clean JSON (no colors)
# @env LLM_OUTPUT=/dev/fd/1     Output path
# @env LLM_OUTPUT_COLOR=0       Set to 1 to enable colored diffs
# @env LLM_PATCH_AWK=           Custom path to patch.awk (defaults to $ROOT_DIR/utils/patch.awk)
# @env LLM_GUARD=               Custom path to guard_operation.sh (defaults to $ROOT_DIR/utils/guard_operation.sh)

set -euo pipefail

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

error() { echo -e "${RED}✗ $1${NC}" >&2; exit 1; }
success() { echo -e "${GREEN}✓ $1${NC}"; }
info() { echo -e "${BLUE}ℹ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}" >&2; }

main() {
    # Resolve tool root directory robustly
    local ROOT_DIR
    ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # ========== VALIDATION ==========
    local path="${argc_path:-}"
    local contents="${argc_contents:-}"
    local dry_run="${argc_dry_run:-false}"
    local backup="${argc_backup:-false}"
    local verbose="${argc_verbose:-false}"
    local json_mode="${argc_json_output:-false}"

    [[ -n "$path" ]]      || { error "Required option --path is missing"; exit 2; }
    [[ -n "$contents" ]]  || { error "Required option --contents is missing"; exit 2; }
    [[ -e "$path" ]]      || { error "Path not found: $path"; exit 2; }

    # Dependency checks
    command -v awk >/dev/null 2>&1     || error "awk is required but not installed"
    command -v mktemp >/dev/null 2>&1  || error "mktemp is required but not installed"
    command -v diff >/dev/null 2>&1    || error "diff is required but not installed"

    local patch_awk="${LLM_PATCH_AWK:-$ROOT_DIR/utils/patch.awk}"
    [[ -f "$patch_awk" ]] || error "patch.awk not found at: $patch_awk"

    if [[ "$json_mode" == "true" ]]; then
        command -v jq >/dev/null 2>&1 || error "jq is required for --json-output"
    fi

    if [[ "$verbose" == "true" ]]; then
        info "Patch target: $path"
        info "Mode: $([[ -d "$path" ]] && echo "directory (batch)" || echo "file (single)")"
    fi

    local patch_file temp_workdir
    patch_file="$(mktemp)"
    temp_workdir="$(mktemp -d)"

    cleanup() {
        rm -f "${patch_file:-}" 2>/dev/null || true
        rm -rf "${temp_workdir:-}" 2>/dev/null || true
    }
    trap cleanup EXIT

    printf "%s\n" "$contents" > "$patch_file"

    # Normalize CRLF to LF
    sed -i 's/\r$//' "$patch_file" 2>/dev/null || true

    local target_type
    if [[ -d "$path" ]]; then
        target_type="directory"
    else
        target_type="file"
    fi

    local applied_files=()
    local new_contents=""

    # ========== DIRECTORY MODE (BATCH) ==========
    if [[ "$target_type" == "directory" ]]; then
        # 1. Parse patch to find target files
        local files_to_patch=()
        while IFS= read -r line; do
            # Fixed regex: bash regex doesn't need backslash escape for +; just escape it once
            if [[ "$line" =~ ^\+\+\+\ +(.*) ]] || [[ "$line" =~ ^---\ +(.*) ]]; then
                local rel_file="${BASH_REMATCH[1]}"
                rel_file="${rel_file%%[	 ]*}"
                # Strip dynamic a/ or b/ prefixes
                if [[ "$rel_file" =~ ^[ab]/(.*) ]]; then
                    rel_file="${BASH_REMATCH[1]}"
                fi
                if [[ "$rel_file" != "/dev/null" && -n "$rel_file" ]]; then
                    files_to_patch+=("$rel_file")
                fi
            fi
        done < "$patch_file"

        local unique_files=()
        if [[ ${#files_to_patch[@]} -gt 0 ]]; then
            while IFS= read -r f; do
                unique_files+=("$f")
            done < <(printf "%s\n" "${files_to_patch[@]}" | awk '!seen[$0]++')
        fi

        if [[ ${#unique_files[@]} -eq 0 ]]; then
            error "No valid target files found in patch contents"
        fi

        if [[ "$verbose" == "true" ]]; then
            info "Target files (${#unique_files[@]}): ${unique_files[*]}"
        fi

        # 2. Replicate directory structures and copy files to the temp directory
        local f
        for f in "${unique_files[@]}"; do
            if [[ -f "$path/$f" ]]; then
                mkdir -p "$(dirname "$temp_workdir/$f")"
                cp "$path/$f" "$temp_workdir/$f"
            fi
        done

        # 3. Apply the patches to the temporary workspace in-place
        if ! (cd "$temp_workdir" && awk -f "$patch_awk" "$patch_file" >/dev/null 2>&1); then
            error "Failed to apply batch patch using awk"
        fi

        # 4. Generate diff previews
        local diff_args=()
        if command -v git >/dev/null 2>&1; then
            diff_args+=("--no-index")
            if [[ "${LLM_OUTPUT_COLOR:-0}" == "1" ]]; then
                diff_args+=("--color=always")
            fi
            for f in "${unique_files[@]}"; do
                if [[ -f "$temp_workdir/$f" ]]; then
                    applied_files+=("$f")
                    if [[ -f "$path/$f" ]]; then
                        git diff "${diff_args[@]}" "$path/$f" "$temp_workdir/$f" 2>/dev/null || true
                    else
                        git diff "${diff_args[@]}" "/dev/null" "$temp_workdir/$f" 2>/dev/null || true
                    fi
                fi
            done
        else
            for f in "${unique_files[@]}"; do
                if [[ -f "$temp_workdir/$f" ]]; then
                    applied_files+=("$f")
                    if [[ -f "$path/$f" ]]; then
                        if [[ "${LLM_OUTPUT_COLOR:-0}" == "1" ]] && command -v colordiff >/dev/null 2>&1; then
                            diff -u "$path/$f" "$temp_workdir/$f" | colordiff || true
                        else
                            diff -u "$path/$f" "$temp_workdir/$f" || true
                        fi
                    else
                        if [[ "${LLM_OUTPUT_COLOR:-0}" == "1" ]] && command -v colordiff >/dev/null 2>&1; then
                            diff -u "/dev/null" "$temp_workdir/$f" | colordiff || true
                        else
                            diff -u "/dev/null" "$temp_workdir/$f" || true
                        fi
                    fi
                fi
            done
        fi

        # 5. Safety guard
        if [[ "$dry_run" == "true" ]]; then
            warn "Dry-run mode: no changes will be written"
        else
            local guard="${LLM_GUARD:-$ROOT_DIR/utils/guard_operation.sh}"
            if [[ -f "$guard" ]]; then
                "$guard" "Apply batch changes to: $path?" || warn "Guard declined; aborting"
            else
                warn "guard_operation.sh not found. Applying changes directly."
            fi

            # 6. Apply modified files back
            for f in "${unique_files[@]}"; do
                if [[ -f "$temp_workdir/$f" ]]; then
                    mkdir -p "$(dirname "$path/$f")"
                    if [[ "$backup" == "true" && -f "$path/$f" ]]; then
                        cp "$path/$f" "$path/$f.bak"
                    fi
                    cp "$temp_workdir/$f" "$path/$f"
                else
                    if [[ -f "$path/$f" ]]; then
                        rm -f "$path/$f"
                    fi
                fi
            done
        fi

    # ========== FILE MODE (SINGLE) ==========
    else
        [[ -r "$path" ]] || { error "File is not readable: $path"; exit 2; }
        [[ -w "$path" ]] || { error "File is not writable: $path"; exit 2; }

        if ! new_contents="$(awk -f "$patch_awk" "$path" "$patch_file" 2>/dev/null)"; then
            error "Failed to generate patch contents using awk"
        fi

        # Run single-file diff preview
        if command -v git >/dev/null 2>&1; then
            local diff_args=("--no-index")
            if [[ "${LLM_OUTPUT_COLOR:-0}" == "1" ]]; then
                diff_args+=("--color=always")
            fi
            printf "%s\n" "$new_contents" | git diff "${diff_args[@]}" "$path" - 2>/dev/null || true
        else
            if [[ "${LLM_OUTPUT_COLOR:-0}" == "1" ]] && command -v colordiff >/dev/null 2>&1; then
                diff -u "$path" <(printf "%s\n" "$new_contents") | colordiff || true
            else
                diff -u "$path" <(printf "%s\n" "$new_contents") || true
            fi
        fi

        # Safety guard
        if [[ "$dry_run" == "true" ]]; then
            warn "Dry-run mode: no changes will be written"
        else
            local guard="${LLM_GUARD:-$ROOT_DIR/utils/guard_operation.sh}"
            if [[ -f "$guard" ]]; then
                "$guard" "Apply changes?" || warn "Guard declined; aborting"
            else
                warn "guard_operation.sh not found. Proceeding with changes."
            fi

            if [[ "$backup" == "true" ]]; then
                cp "$path" "$path.bak"
            fi
            printf "%s\n" "$new_contents" > "$path"
        fi
    fi

    # ========== OUTPUT ==========
    if [[ "$json_mode" == "true" ]]; then
        local files_json
        if [[ "$target_type" == "directory" ]]; then
            files_json=$(printf '%s\n' "${applied_files[@]}" | jq -R . | jq -s .)
        else
            files_json="[]"
        fi
        jq -n \
            --arg status "success" \
            --arg path "$path" \
            --arg type "$target_type" \
            --argjson dry "$dry_run" \
            --argjson backup "$backup" \
            --argjson files "$files_json" \
            '{status:$status, path:$path, type:$type, dry_run:$dry, backup:$backup, files:$files}'
    else
        success "Patch applied to: $path"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
