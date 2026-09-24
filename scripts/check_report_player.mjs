// Check queued media events when switching clips; a paused old player must not
// read the new player's state and leave its old button labelled as playing.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const handlers = {}, players = new Set();
class Audio {
  paused = true;
  events = {};
  constructor() { players.add(this); }
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute() {}
  async play() { this.paused = false; queueMicrotask(() => this.events.play()); }
  pause() { this.paused = true; queueMicrotask(() => this.events.pause()); }
  remove() { players.delete(this); }
}
const document = {
  addEventListener(name, handler) { handlers[name] = handler; },
  createElement(tag) { assert.equal(tag, 'audio'); return new Audio(); },
  getElementById() { return { addEventListener() {} }; },
};
runInNewContext(readFileSync(new URL('../apps/sidecar/data/report/report.js', import.meta.url), 'utf8'), {
  document, location: { hash: '' }, addEventListener() {},
});
function button() {
  return { dataset: { audio: 'test.wav', play: 'Play', pause: 'Pause' },
    attributes: {}, textContent: 'Play',
    getAttribute() { return 'Test clip'; },
    setAttribute(name, value) { this.attributes[name] = value; }, after() {} };
}
const first = button(), second = button();
const click = target => handlers.click({ target: { closest: () => target } });
await click(first);
assert.equal(first.attributes['aria-pressed'], 'true');
await click(second);
assert.equal(players.size, 1);
assert.equal(first.textContent, 'Play');
assert.equal(first.attributes['aria-pressed'], 'false');
assert.equal(second.attributes['aria-pressed'], 'true');
await click(second);
assert.equal(second.textContent, 'Play');
assert.equal(second.attributes['aria-pressed'], 'false');
console.log('Report player: switching and stopping clips passed.');
