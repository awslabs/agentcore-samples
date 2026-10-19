// Runs the test fake box as a container on :8080, standing in for devbox-box (./run.sh smoke).
import { startFakeBox } from './fake-box.mjs';

await startFakeBox(8080, '0.0.0.0');
console.log('[stub-box] listening on 0.0.0.0:8080');
