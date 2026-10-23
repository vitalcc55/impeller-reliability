import { mkdtemp, mkdir, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { isProjectOutsideInstallation } from './project-location';

describe('project data location', () => {
  let root: string;
  let installation: string;
  let resources: string;

  beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), 'impeller-location-'));
    installation = join(root, 'Installation');
    resources = join(root, 'Resource payload');
    await mkdir(installation);
    await mkdir(resources);
  });

  afterEach(async () => rm(root, { recursive: true, force: true }));

  it('allows a user path with spaces and Cyrillic and a similarly named neighbour', async () => {
    for (const path of [
      join(root, 'Дела пользователя', 'Новое.irproj'),
      `${installation}-copy/a.irproj`,
    ]) {
      expect(await isProjectOutsideInstallation(path, [installation, resources])).toBe(true);
    }
  });

  it('rejects the program roots and new descendants before creating anything', async () => {
    for (const path of [
      installation,
      join(installation, 'new', 'a.irproj'),
      join(resources, 'a.irproj'),
    ]) {
      expect(await isProjectOutsideInstallation(path, [installation, resources])).toBe(false);
    }
  });

  it('rejects a project reached through a junction into the installed payload', async () => {
    const alias = join(root, 'Alias');
    await symlink(installation, alias, 'junction');
    expect(
      await isProjectOutsideInstallation(join(alias, 'new', 'a.irproj'), [installation, resources]),
    ).toBe(false);
  });

  it('rejects a lexical path inside the installation even if a junction points outside', async () => {
    const user = join(root, 'Documents');
    await mkdir(user);
    await symlink(user, join(installation, 'Alias'), 'junction');
    expect(
      await isProjectOutsideInstallation(join(installation, 'Alias', 'a.irproj'), [
        installation,
        resources,
      ]),
    ).toBe(false);
  });

  it('resolves an aliased resources root and fails closed on filesystem errors', async () => {
    const alias = join(root, 'Resources alias');
    await symlink(resources, alias, 'junction');
    expect(
      await isProjectOutsideInstallation(join(resources, 'a.irproj'), [installation, alias]),
    ).toBe(false);
    await expect(
      isProjectOutsideInstallation('bad\0path.irproj', [installation]),
    ).rejects.toThrow();
  });
});
