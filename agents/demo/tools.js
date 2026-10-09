/**
 * Get the system info
 */
exports.get_ipinfo = async function () {
   try {
      const res = await fetch("https://httpbin.org/ip");
      if (!res.ok) {
         return { ip: "127.0.0.1" };
      }
      const data = await res.json();
      return data;
   } catch (err) {
      return { ip: "127.0.0.1" };
   }
}
