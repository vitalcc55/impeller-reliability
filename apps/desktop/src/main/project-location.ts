import { realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';

function contains(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return (
    suffix === '' || (suffix !== '..' && !suffix.startsWith(`..${sep}`) && !isAbsolute(suffix))
  );
}

// New projects may have several missing parents; resolve the existing ancestor
// so a junction cannot place user data in the replaceable program payload.
async function canonicalPath(path: string): Promise<string> {
  try {
    return await realpath(path);
  } catch (error: unknown) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') throw error;
    const parent = dirname(path);
    if (parent === path) throw error;
    return join(await canonicalPath(parent), basename(path));
  }
}

export async function isProjectOutsideInstallation(
  path: string,
  programRoots: readonly string[],
): Promise<boolean> {
  const absolutePath = resolve(path);
  if (programRoots.some((root) => contains(resolve(root), absolutePath))) return false;
  const candidate = await canonicalPath(absolutePath);
  for (const root of programRoots) {
    if (contains(await realpath(root), candidate)) return false;
  }
  return true;
}
