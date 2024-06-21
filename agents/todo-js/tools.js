const fs = require('fs');
const path = require('path');

/**
 * Add a new todo item
 * @typedef {Object} AddTodoArgs
 * @property {string} desc - The task description
 * @param {AddTodoArgs} args
 */
exports.add_todo = function addTodo(args) {
  const todosFile = _getTodosFile();
  if (fs.existsSync(todosFile)) {
    const num = JSON.parse(fs.readFileSync(todosFile)).reduce((max, item) => Math.max(max, item.id), 0) + 1;
    const data = fs.readFileSync(todosFile);
    fs.writeFileSync(todosFile, JSON.stringify([...JSON.parse(data), { id: num, desc: args.desc }]));
    console.log(`Successfully added todo id=${num}`);
  } else {
    fs.writeFileSync(todosFile, JSON.stringify([{ id: 1, desc: args.desc }]));
    console.log('Successfully added todo id=1');
  }
}

/**
 * Delete an existing todo item
 * @typedef {Object} DelTodoArgs
 * @property {number} id - The task id
 * @param {DelTodoArgs} args
 */
exports.del_todo = function delTodo(args) {
  const todosFile = _getTodosFile();
  if (fs.existsSync(todosFile)) {
    const data = fs.readFileSync(todosFile);
    const newData = JSON.parse(data).filter(item => item.id !== args.id);
    fs.writeFileSync(todosFile, JSON.stringify(newData));
    console.log(`Successfully deleted todo id=${args.id}`);
  } else {
    console.log('Empty todo list');
  }
}

/**
 * Display the current todo list in json format.
 */
exports.list_todos = function listTodos() {
  const todosFile = _getTodosFile();
  if (fs.existsSync(todosFile)) {
    console.log(fs.readFileSync(todosFile, "utf8"));
  } else {
    console.log("[]");
  }
}

/**
 * Delete the entire todo list.
 */
exports.clear_todos = function clearTodos() {
  const todosFile = _getTodosFile();
  fs.unlinkSync(todosFile)
  console.log("Successfully deleted entry todo list");
}

function _getTodosFile() {
  const cacheDir = process.env.LLM_AGENT_CACHE_DIR || '/tmp';
  if (!fs.existsSync(cacheDir)) {
    fs.mkdirSync(cacheDir, { recursive: true });
  }
  return path.join(cacheDir, 'todos.json');
}
