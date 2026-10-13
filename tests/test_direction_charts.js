const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Load the actual page functions; DOMContentLoaded stays deferred as in a browser.
const context = vm.createContext({ window: { addEventListener() {} } });
vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/app.js"), "utf8"), context);
const points = [
  { timestamp: 1, course: 359, heading: 20, course_break: true, heading_break: true },
  { timestamp: 2, course: 1, heading: 30, course_break: true, heading_break: false },
  { timestamp: 3, course: 2, heading: null, course_break: false, heading_break: true },
  { timestamp: 4, course: 3, heading: 40, course_break: false, heading_break: true },
];
const course = JSON.parse(JSON.stringify(context.directionLineData(points, "course")));
const heading = JSON.parse(JSON.stringify(context.directionLineData(points, "heading")));
assert.deepEqual(course, [{ x: 1000, y: 359 }, { x: 2000, y: null },
  { x: 2000, y: 1 }, { x: 3000, y: 2 }, { x: 4000, y: 3 }]);
assert.equal(heading.length, 6);
assert.deepEqual(heading.slice(-2), [{ x: 4000, y: null }, { x: 4000, y: 40 }]);
assert.equal(heading[1].y, 30); // A COG wrap must not break a continuous heading.
assert.deepEqual(JSON.parse(JSON.stringify(context.directionLineData([], "course"))), []);
console.log("Direction chart gaps: passed");
