const http=require('http'),fs=require('fs'),path=require('path'),zlib=require('zlib');
const web=path.resolve(__dirname,'..'),root=path.join(web,'dist');
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.wasm':'application/wasm','.json':'application/json','.py':'text/plain; charset=utf-8','.zip':'application/zip','.md':'text/plain; charset=utf-8'};
function createServer(){return http.createServer((req,res)=>{
 if(!['GET','HEAD'].includes(req.method)){res.writeHead(405);return res.end()}
 let relative;try{relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname)}catch{res.writeHead(400);return res.end()}
 const file=path.resolve(root,'.'+(relative==='/'?'/index.html':relative));
 if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end('Not found')}
 res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Cache-Control','no-cache');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Vary','Accept-Encoding');
 if(req.method==='HEAD')return res.end();
 const source=fs.createReadStream(file);source.on('error',()=>res.destroy());
 if(/gzip/.test(req.headers['accept-encoding']||'')){res.setHeader('Content-Encoding','gzip');source.pipe(zlib.createGzip()).pipe(res)}else source.pipe(res);
})}
if(require.main===module){const port=Number(process.env.PORT||4173);const server=createServer();server.listen(port,'127.0.0.1',()=>console.log(`RailNomad webpage: http://127.0.0.1:${port}`));server.on('error',e=>{console.error(e.code==='EADDRINUSE'?'Port in use. Open http://127.0.0.1:'+port:e.message);process.exitCode=1})}
module.exports={createServer};
