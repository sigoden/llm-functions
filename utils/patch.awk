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
