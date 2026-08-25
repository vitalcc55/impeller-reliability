import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import process from 'node:process';

const rendererHtml = await readFile(resolve('out/renderer/index.html'), 'utf8');
if (!rendererHtml.includes("connect-src 'none'")) {
  throw new Error('production_csp_does_not_block_connections');
}
if (/\b(?:ws|wss|http|https):\/\//u.test(rendererHtml)) {
  throw new Error('production_csp_contains_network_origin');
}
if (rendererHtml.includes('__IMPELLER_CSP__')) {
  throw new Error('production_csp_placeholder_not_replaced');
}
process.stdout.write('Production CSP verified: connect-src none.\n');
