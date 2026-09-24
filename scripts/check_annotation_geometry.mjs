import assert from 'node:assert/strict';
import {scaledZoom,sourceTime} from '../apps/web/src/lib/annotation-geometry.js';
for(const zoom of [{x:1,y:1},{x:2,y:4},{x:8,y:4}]){
  for(const factor of [.1,.9,1.4,20]){const next=scaledZoom(zoom,'both',factor);assert.equal(next.x/next.y,zoom.x/zoom.y);assert.ok(Math.min(next.x,next.y)>=1&&Math.max(next.x,next.y)<=8);}
  assert.equal(scaledZoom(zoom,'x',1.4).y,zoom.y);assert.equal(scaledZoom(zoom,'y',1.4).x,zoom.x);
}
// Source position survives zoom and horizontal panning; view is only a projection.
assert.equal(sourceTime(300,100,600,120,135),125);
assert.equal(sourceTime(500,-100,1800,120,135),125);
assert.equal(sourceTime(-1000,100,600,120,135),120);
assert.equal(sourceTime(1000,100,600,120,135),135);
console.log('Annotation geometry: passed');
