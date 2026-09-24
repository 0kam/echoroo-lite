// Serve only this prototype and its assets; byte ranges let native audio seek.
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import sirv from 'sirv';

const files=new Set([
 '/docs/prototypes/annotation.html','/docs/prototypes/annotation.js',
 '/apps/web/src/lib/theme.css','/apps/web/static/echoroo.png',
 '/.local-data/annotation-design/preview.json',
]);
const serve=sirv(fileURLToPath(new URL('../',import.meta.url)),{
 dev:true,dotfiles:true,setHeaders(res){res.setHeader('Accept-Ranges','bytes');res.setHeader('Cache-Control','no-store');},
});
const server=createServer((req,res)=>{
 const path=(req.url||'').split('?')[0];
 if(!files.has(path)&&!/^\/\.local-data\/annotation-design\/clip-\d+\.(wav|png)$/.test(path)){
  res.writeHead(404);res.end();return;
 }
 serve(req,res);
});
server.listen(Number(process.argv[2]??51750),'127.0.0.1',()=>{
 console.log(`http://127.0.0.1:${server.address().port}/docs/prototypes/annotation.html`);
});
