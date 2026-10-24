// Real compiled React build and HTTP proxy, with no development client/HMR.
import {preview} from '../frontend/node_modules/vite/dist/node/index.js';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../frontend/',import.meta.url));
await preview({root,preview:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/api':{target:'http://127.0.0.1:8000',changeOrigin:false}}}});
console.log('Isolated production-build preview/proxy ready; no development reload client');
