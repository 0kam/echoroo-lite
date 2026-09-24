/** @param {{x:number,y:number}} current @param {'x'|'y'|'both'} axis @param {number} factor */
export function scaledZoom(current, axis, factor) {
  if (axis === 'both') factor = Math.max(1 / Math.min(current.x, current.y), Math.min(8 / Math.max(current.x, current.y), factor));
  return { x: axis === 'y' ? current.x : Math.max(1, Math.min(8, current.x * factor)),
    y: axis === 'x' ? current.y : Math.max(1, Math.min(8, current.y * factor)) };
}

/** @param {number} x @param {number} left @param {number} width @param {number} start @param {number} end */
export function sourceTime(x, left, width, start, end) {
  return Math.round((start + Math.max(0, Math.min(1, (x - left) / width)) * (end - start)) * 100) / 100;
}
