const os = require("node:os");
/**
 * Get the system info
 */
exports.get_sysinfo = function getSysinfo() {
    return `OS: ${os.type()}
Arch: ${os.arch()}
User: ${process.env["USER"]}`
}
