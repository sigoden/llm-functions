import {
    AST_Export,
    AST_Import,
    AST_Toplevel,
} from "../ast.js";

AST_Toplevel.DEFMETHOD("optimize_import_export", function(compressor) {
    if (!compressor.option("import_export")) return;

    // We will compare `stat` against `prev` to see if it's a compatible import/export
    // When compatible, merge!
    let stat_i = 1;

    while (stat_i < this.body.length) {
        const stat = this.body[stat_i];
        const prev = this.body[stat_i - 1];

        const merged_import = 
            (stat instanceof AST_Import && prev instanceof AST_Import)
            && merge_imports(prev, stat);
        const merged_export = 
            (stat instanceof AST_Export && prev instanceof AST_Export)
            && merge_exports(prev, stat);

        if (merged_import || merged_export) {
            this.body.splice(stat_i, 1);
            // Don't stat_i++. We can stay here.
        } else {
            stat_i++;
        }
    }
});

/** Merge a compatible import statement. Or return false */
function merge_imports(into, merge_from) {
    if (
        // same "from"
        into.module_name.value === merge_from.module_name.value
        // only one can have a default import
        && !(into.imported_name && merge_from.imported_name)
        // "*" not supported
        && can_merge_name_mappings(into.imported_names, merge_from.imported_names)
        // "with" not supported
        && !into.attributes && !merge_from.attributes
    ) {
        into.imported_name = into.imported_name || merge_from.imported_name;
        into.imported_names = merge_name_mappings(into.imported_names, merge_from.imported_names);

        return true; // `merge_from` can be safely removed
    } else {
        return false;
    }
}

/** Merge a compatible export statement. Or return false */
function merge_exports(into, merge_from) {
    if (
        // "export function" not supported
        !into.exported_value && !merge_from.exported_value
        // "export from" not supported
        && !into.module_name && !merge_from.module_name
        // "export default" not supported
        && !into.is_default && !merge_from.is_default
        // "with" not supported
        && !into.attributes && !merge_from.attributes
    ) {
        if (
            // "export { a, b, c }"
            into.exported_names && merge_from.exported_names
            && can_merge_name_mappings(into.exported_names, merge_from.exported_names)
        ) {
            into.exported_names = merge_name_mappings(into.exported_names, merge_from.exported_names);

            return true;
        } else if (
            // "export var xx"
            into.exported_definition && merge_from.exported_definition
            && can_merge_definitions(into.exported_definition, merge_from.exported_definition)
        ) {
            merge_definitions(into.exported_definition, merge_from.exported_definition);

            return true;
        } else {
            return false;
        }
    } else {
        return false;
    }
}

/** Make sure a and b are optional AST_NameMapping*? arrays without the same names */
function can_merge_name_mappings(a, b) {
    for (const mapping_array of [a, b]) {
        for (const mapping of (mapping_array || [])) {
            if (mapping.name.name === "*") return false;
            if (mapping.foreign_name.name === "*") return false;
        }
    }

    return true;
}

function merge_name_mappings(our_names, their_names) {
    const arr_our_names = our_names || [];
    const arr_their_names = their_names || [];

    if (arr_our_names.length + arr_their_names.length > 0) {
        return arr_our_names.concat(arr_their_names);
    } else {
        return our_names;
    }
}

function can_merge_definitions(our_defs, their_defs) {
    return our_defs.TYPE === their_defs.TYPE;
}

/** Merge two AST_Definitions */
function merge_definitions(our_defs, their_defs) {
    our_defs.definitions.push(...their_defs.definitions);
}
