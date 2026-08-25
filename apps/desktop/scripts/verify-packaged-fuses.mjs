import { resolve } from 'node:path';
import process from 'node:process';

import { verifyProductionFuses } from './fuse-policy.mjs';

const executableArgument = process.argv[2];
if (executableArgument === undefined) throw new Error('packaged_executable_path_required');
await verifyProductionFuses(resolve(executableArgument));
process.stdout.write('Production Electron fuses verified.\n');
