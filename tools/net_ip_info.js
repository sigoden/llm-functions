const { execSync } = require("child_process");
const os = require("os");

function getPublicIP() {
  try {
    const ipify = execSync("curl -s https://api.ipify.org", { encoding: "utf8" }).trim();
    return ipify;
  } catch {
    try {
      const ipinfo = execSync("curl -s https://ipinfo.io/ip", { encoding: "utf8" }).trim();
      return ipinfo;
    } catch {
      return "N/A";
    }
  }
}

function getNetworkInterfaces() {
  const interfaces = os.networkInterfaces();
  const result = {};

  for (const [name, addrs] of Object.entries(interfaces)) {
    const validAddrs = addrs
      .filter((addr) => addr.family === "IPv4" && !addr.internal)
      .map((addr) => ({
        address: addr.address,
        netmask: addr.netmask,
        mac: addr.mac,
      }));

    if (validAddrs.length > 0) {
      result[name] = validAddrs;
    }
  }

  return result;
}

/**
 * Get network IP information including public IP and network interfaces
 * 
 * @describe Get network IP information including public IP and network interfaces
 * @option --public-ip Include public IP address (default: true)
 * @option --interfaces Include network interface information (default: true)
 * 
 * @typedef {Object} Args
 * @property {boolean} [public_ip] Include public IP address
 * @property {boolean} [interfaces] Include network interface information
 * @param {Args} args
 */
exports.run = function (args) {
  const { public_ip = true, interfaces = true } = args;
  const output = {};

  if (public_ip) {
    output.public_ip = getPublicIP();
  }

  if (interfaces) {
    output.interfaces = getNetworkInterfaces();
  }

  return output;
};
