import { appendFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';

export interface LogEvent {
  readonly severity: 'info' | 'warning' | 'error';
  readonly component: string;
  readonly event: string;
  readonly requestId?: string;
  readonly errorCode?: string;
  readonly details?: Readonly<Record<string, unknown>>;
}

export class JsonlLogger {
  public constructor(public readonly filePath: string) {}

  public async write(event: LogEvent): Promise<void> {
    await mkdir(dirname(this.filePath), { recursive: true });
    const line = JSON.stringify({
      schemaVersion: 1,
      timestampUtc: new Date().toISOString(),
      service: 'impeller-reliability-desktop',
      ...event,
    });
    await appendFile(this.filePath, `${line}\n`, 'utf8');
  }
}
