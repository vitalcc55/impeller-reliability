import {
  flipFuses,
  FuseState,
  FuseVersion,
  FuseV1Options,
  getCurrentFuseWire,
} from '@electron/fuses';

const expectedFuses = new Map([
  [FuseV1Options.RunAsNode, false],
  [FuseV1Options.EnableCookieEncryption, true],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, false],
  [FuseV1Options.EnableNodeCliInspectArguments, false],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, true],
  [FuseV1Options.OnlyLoadAppFromAsar, true],
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, false],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, false],
  [FuseV1Options.WasmTrapHandlers, true],
]);

const fuseConfig = {
  version: FuseVersion.V1,
  strictlyRequireAllFuses: true,
  [FuseV1Options.RunAsNode]: false,
  [FuseV1Options.EnableCookieEncryption]: true,
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
  [FuseV1Options.EnableNodeCliInspectArguments]: false,
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
  [FuseV1Options.OnlyLoadAppFromAsar]: true,
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot]: false,
  [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
  [FuseV1Options.WasmTrapHandlers]: true,
};

export async function verifyProductionFuses(executablePath) {
  const current = await getCurrentFuseWire(executablePath);
  if (current.version !== FuseVersion.V1) throw new Error('unexpected_electron_fuse_version');
  for (const [option, enabled] of expectedFuses) {
    const expectedState = enabled ? FuseState.ENABLE : FuseState.DISABLE;
    if (current[option] !== expectedState) {
      throw new Error(`electron_fuse_mismatch:${String(option)}:${String(current[option])}`);
    }
  }
}

export async function applyProductionFuses(executablePath) {
  await flipFuses(executablePath, fuseConfig);
  await verifyProductionFuses(executablePath);
}
