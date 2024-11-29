#!/usr/bin/awk -f

# Apply a diff file to an original
# Usage: awk -f patch.awk target-file patch-file

FNR == NR {
    lines[FNR] = $0
    next;
}

{
    patchLines[FNR] = $0
}

END {
    totalPatchLines=length(patchLines)
    totalLines = length(lines)
    patchLineIndex = 1

    mode = "none"

    while (patchLineIndex <= totalPatchLines) {
        line = patchLines[patchLineIndex]

        if (line ~ /^--- / || line ~ /^\+\+\+ /) {
            patchLineIndex++
            continue
        }

        if (line ~ /^@@ /) {
            mode = "hunk"
            hunkIndex++
            patchLineIndex++
            continue
        }

        if (mode == "hunk") {
            while (patchLineIndex <= totalPatchLines && line ~ /^[-+ ]|^\s*$/ && line !~ /^--- /) {
                sanitizedLine = substr(line, 2)
                if (line !~ /^\+/) {
                    hunkTotalOriginalLines[hunkIndex]++;
                    hunkOriginalLines[hunkIndex,hunkTotalOriginalLines[hunkIndex]] = sanitizedLine
                } 
                if (line !~ /^-/) {
                    hunkTotalUpdatedLines[hunkIndex]++;
                    hunkUpdatedLines[hunkIndex,hunkTotalUpdatedLines[hunkIndex]] = sanitizedLine
                }
                patchLineIndex++
                line = patchLines[patchLineIndex]
            }
            mode = "none"
        } else {
            patchLineIndex++
        }
    }

    if (hunkIndex == 0) {
        print "error: no patch" > "/dev/stderr"
        exit 1
    }

    totalHunks = hunkIndex
    hunkIndex = 1

    # inspectHunks()

    for (lineIndex = 1; lineIndex <= totalLines; lineIndex++) {
        line = lines[lineIndex]
        nextLineIndex = 0

        if (hunkIndex <= totalHunks && line == hunkOriginalLines[hunkIndex,1]) {
            nextLineIndex = lineIndex + 1
            for (i = 2; i <= hunkTotalOriginalLines[hunkIndex]; i++) {
                if (lines[nextLineIndex] != hunkOriginalLines[hunkIndex,i]) {
                    nextLineIndex = 0
                    break
                }
                nextLineIndex++
            }
        }
        if (nextLineIndex > 0) {
            for (i = 1; i <= hunkTotalUpdatedLines[hunkIndex]; i++) {
                print hunkUpdatedLines[hunkIndex,i]
            }
            hunkIndex++
            lineIndex = nextLineIndex - 1;
        } else {
            print line
        }
    }

    if (hunkIndex != totalHunks + 1) {
        print "error: unable to apply patch" > "/dev/stderr"
        exit 1
    }
}

function inspectHunks() {
    print "/* Begin inspecting hunks"
    for (i = 1; i <= totalHunks; i++) {
        print ">>>>>> Original"
        for (j = 1; j <= hunkTotalOriginalLines[i]; j++) {
            print hunkOriginalLines[i,j]
        }
        print "======"
        for (j = 1; j <= hunkTotalUpdatedLines[i]; j++) {
            print hunkUpdatedLines[i,j]
        }
        print "<<<<<< Updated"
    }
    print "End inspecting hunks */\n"
}