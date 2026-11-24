import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

interface Target { name: string; account: string; region: string }

export function assertFreshPortState(root: string, projectName: string, targets: Target[]) {
  for (const file of ['deploy_outputs.json', '.bedrock_agentcore.yaml', 'agentcore/.bedrock_agentcore.yaml', 'agentcore/deployed-state.json']) {
    if (existsSync(resolve(root, file))) throw new Error(`Legacy state ${file}; adoption is unsupported. Do not delete it to bypass this guard.`);
  }
  const statePath = resolve(root, 'agentcore/.cli/deployed-state.json');
  const markerPath = resolve(root, 'agentcore/fresh-port.json');
  if (!targets.length && !existsSync(statePath)) return; // target-less offline build
  if (!existsSync(markerPath)) throw new Error('Run deploy.py --fresh in a separate undeployed checkout first');
  const marker = JSON.parse(readFileSync(markerPath, 'utf8'));
  if (marker.version !== 1 || marker.projectName !== projectName || JSON.stringify(marker.targets) !== JSON.stringify(targets)) {
    throw new Error('Fresh-port marker does not match the target configuration');
  }
  if (!existsSync(statePath)) return;
  const state = JSON.parse(readFileSync(statePath, 'utf8'));
  if (Object.keys(state).some(k => k !== 'targets') || !state.targets) throw new Error('Unknown/legacy CLI state');
  for (const [name, entry] of Object.entries(state.targets) as [string, any][]) {
    const target = targets.find(t => t.name === name);
    if (!target || Object.keys(entry).some(k => !['stackArn', 'resources'].includes(k)) ||
        Object.keys(entry.resources ?? {}).some(k => k !== 'credentials') ||
        Object.keys(entry.resources?.credentials ?? {}).length) throw new Error('Legacy or unexpected resources in CLI state');
    const expected = `arn:aws:cloudformation:${target.region}:${target.account}:stack/AgentCore-${projectName}-${name}/`;
    if (entry.stackArn !== undefined && (!entry.stackArn.startsWith(expected) || entry.stackArn.length <= expected.length)) {
      throw new Error('CLI state points at a different stack; adoption is unsupported');
    }
  }
}
