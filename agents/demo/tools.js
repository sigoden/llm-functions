/**
 * Get the system info
 */
exports.get_ipinfo = async function () {
   const res = await fetch("https://httpbin.org/ip")
   return res.json();
}
