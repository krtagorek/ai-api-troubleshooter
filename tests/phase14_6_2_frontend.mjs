// Isolated real Vite frontend/proxy. HMR disabled to avoid reloading memory-only auth.
import {createServer} from '../frontend/node_modules/vite/dist/node/index.js';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../frontend/',import.meta.url));
const server=await createServer({root,server:{host:'127.0.0.1',port:5173,strictPort:true,hmr:false}});
await server.listen();console.log('Isolated real frontend/proxy ready; HMR false; no credentials configured');
