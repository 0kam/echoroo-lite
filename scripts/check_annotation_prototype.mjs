// Check absolute time under zoom/pan and prevent unmarked species becoming negatives.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source=readFileSync(new URL('../docs/prototypes/annotation.js',import.meta.url),'utf8');
const html=readFileSync(new URL('../docs/prototypes/annotation.html',import.meta.url),'utf8');
const core=source.slice(0,source.indexOf("$('locale').onchange"));
const {timeAt,scaledZoom,annotationAnswer,messages}=runInNewContext(core+';({timeAt,scaledZoom,annotationAnswer,messages})',{
 document:{getElementById(){}},navigator:{language:'en'},location:{href:'http://localhost/prototype',search:''},URL,URLSearchParams,
});
const viewport={left:100,right:900};
assert.equal(timeAt(300,{left:100,width:800},viewport),3.75);
// At 2x zoom and 400 px pan, the viewport shows 3.75–11.25 seconds.
assert.equal(timeAt(100,{left:-300,width:1600},viewport),3.75);
assert.equal(timeAt(900,{left:-300,width:1600},viewport),11.25);
assert.equal(timeAt(950,{left:-300,width:1600},viewport),11.25);
assert.equal(timeAt(50,{left:-300,width:1600},viewport),3.75);
// A timestamp maps back to itself when a viewport zooms around its centre.
for(const zoom of [1,2,4,8]){
 const width=800*zoom,left=500-7.5/15*width;
 assert.equal(timeAt(500,{left,width},viewport),7.5);
}
// Ordinary zoom preserves the current aspect ratio, even at either limit.
for(const current of [{x:1,y:1},{x:2,y:3},{x:6,y:8}]){
 for(const factor of [.01,.5,2,100]){
  const next=scaledZoom(current,'both',factor);
  assert.ok(Math.abs(next.x/next.y-current.x/current.y)<1e-12);
  assert.ok(next.x>=1&&next.y>=1&&next.x<=8&&next.y<=8);
 }
}
assert.equal(scaledZoom({x:2,y:3},'x',2).y,3);
assert.equal(scaledZoom({x:2,y:3},'y',2).x,2);
assert.equal(scaledZoom({x:2,y:3},'x',2).x,4);
assert.equal(scaledZoom({x:2,y:3},'y',2).y,6);
const clip={saved:true,ranges:[{species_id:'A',start:2,end:3},{species_id:null,start:6,end:7}],checks:{B:'absent',C:'unknown'}};
assert.equal(annotationAnswer(clip,'A'),'present');
assert.equal(annotationAnswer(clip,'B'),'absent');
assert.equal(annotationAnswer(clip,'C'),'unknown');
assert.equal(annotationAnswer(clip,'D'),null);
assert.equal(annotationAnswer(undefined,'D'),null);
assert.deepEqual(Object.keys(messages.ja).sort(),Object.keys(messages.en).sort());
for(const [,key] of html.matchAll(/data-msg="([^"]+)"/g))assert.ok(messages.ja[key]&&messages.en[key],key);
for(const text of Object.values(messages.ja))assert.doesNotMatch(text,/記録した声|声を記録|声の記録/);
console.log('Annotation prototype: coordinates, proportional/axis zoom, explicit answers, and locales passed.');
