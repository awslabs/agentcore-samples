import { AgentCoreApplication } from '@aws/agentcore-cdk';
import { CfnResource, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as logs from 'aws-cdk-lib/aws-logs';

/** Sample-scoped deliveries and log groups; never changes account Transaction Search. */
export function addObservability(stack: Stack, application: AgentCoreApplication, prefix: string) {
  const group = (id: string, name: string) => new logs.LogGroup(stack, id, {
    logGroupName: name, retention: logs.RetentionDays.ONE_WEEK, removalPolicy: RemovalPolicy.DESTROY,
  });
  // Own Lambda/helper logs explicitly, rather than leave implicit orphan log groups.
  for (const child of stack.node.findAll()) {
    const fn = child as CfnResource;
    if (fn.cfnResourceType !== 'AWS::Lambda::Function') continue;
    const id = stack.getLogicalId(fn);
    const log = group(`Log${id}`, `/aws/lambda/${prefix}-${id}`);
    fn.addPropertyOverride('LoggingConfig.LogGroup', log.logGroupName);
    fn.addResourceDependency(log.node.defaultChild as CfnResource);
  }
  const build = stack.node.findAll().find(c => (c as CfnResource).cfnResourceType === 'AWS::CodeBuild::Project') as CfnResource;
  if (build) {
    const log = group('ContainerBuildLogs', `/aws/codebuild/${stack.stackName}-container-builder`);
    build.addPropertyOverride('LogsConfig.CloudWatchLogs', { Status: 'ENABLED', GroupName: log.logGroupName });
    build.addResourceDependency(log.node.defaultChild as CfnResource);
  }
  const runtime = application.runtime('claimsagent');
  const runtimeLogs = group('RuntimeLogs', `/aws/bedrock-agentcore/runtimes/${runtime.runtimeId}-DEFAULT`);
  application.onlineEvalConfig('ClaimsEvaluation').node.addDependency(runtimeLogs);

  // The original optional helper enabled these resource-level streams. CFN now
  // owns exact names and teardown, with no regional name matching or account setup.
  for (const [kind, arn] of [
    ['gateway', application.gateway('ClaimsGateway').gatewayArn],
    ['memory', application.memory('ClaimsAgentMemory').memoryArn],
  ]) {
    const log = group(`${kind}Logs`, `/aws/vendedlogs/bedrock-agentcore/${prefix}/${kind}`);
    for (const [type, destinationType] of [['APPLICATION_LOGS', 'CWL'], ['TRACES', 'XRAY']]) {
      const name = `${prefix}-${kind}-${destinationType}`;
      const source = new logs.CfnDeliverySource(stack, `${kind}${type}Source`, {
        name, resourceArn: arn, logType: type,
      });
      const destination = new logs.CfnDeliveryDestination(stack, `${kind}${type}Destination`, {
        name, deliveryDestinationType: destinationType,
        ...(destinationType === 'CWL' ? { destinationResourceArn: log.logGroupArn } : {}),
      });
      new logs.CfnDelivery(stack, `${kind}${type}Delivery`, {
        deliverySourceName: source.name, deliveryDestinationArn: destination.attrArn,
      }).addResourceDependency(source);
    }
  }
}
