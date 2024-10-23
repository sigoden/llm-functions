const vm = require('vm');

/**
 * Execute the javascript code in node.js.
 * @typedef {Object} Args
 * @property {string} code - Javascript code to execute, such as `console.log("hello world")`
 * @param {Args} args
 */
exports.run = function run({ code }) {
  const context = vm.createContext({});
  const script = new vm.Script(code);
  return script.runInContext(context);
}
