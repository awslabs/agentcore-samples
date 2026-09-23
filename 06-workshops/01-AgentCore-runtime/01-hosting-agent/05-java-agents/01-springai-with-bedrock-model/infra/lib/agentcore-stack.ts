import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import { Construct } from 'constructs';

export interface AgentCoreStackProps extends cdk.StackProps {
  agentName: string;
  imageTag?: string;
  firstRun?: boolean;
}

export class AgentCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const agentName = props.agentName.replace(/-/g, '_');
    const imageTag = props.imageTag || 'latest';
    const firstRun = props.firstRun ?? false;

    // ECR Repository
    const ecrRepo = new ecr.Repository(this, 'AgentRepository', {
      repositoryName: `${agentName}-repo`,
      imageScanOnPush: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      lifecycleRules: [{ maxImageCount: 10, rulePriority: 1, tagStatus: ecr.TagStatus.ANY }],
    });

    new cdk.CfnOutput(this, 'EcrRepositoryUri', { value: ecrRepo.repositoryUri });

    if (firstRun) {
      new cdk.CfnOutput(this, 'NextSteps', {
        value: `Push image: ./agent/build-and-push.sh -r ${this.region} -u ${ecrRepo.repositoryUri} -t ${imageTag}`,
      });
      return;
    }

    // IAM Role
    const role = new iam.Role(this, 'AgentRuntimeRole', {
      roleName: `${agentName}-agentcore-runtime-role`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': `arn:aws:bedrock-agentcore:${this.region}:${this.account}:*` },
        },
      }),
    });

    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
      resources: [ecrRepo.repositoryArn],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ecr:GetAuthorizationToken'],
      resources: ['*'],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['arn:aws:bedrock:*::foundation-model/*', `arn:aws:bedrock:${this.region}:${this.account}:*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:DescribeLogStreams'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:DescribeLogGroups'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`],
    }));

    // AgentCore Runtime
    const runtime = new bedrockagentcore.CfnRuntime(this, 'AgentCoreRuntime', {
      agentRuntimeName: agentName,
      agentRuntimeArtifact: { containerConfiguration: { containerUri: `${ecrRepo.repositoryUri}:${imageTag}` } },
      roleArn: role.roleArn,
      networkConfiguration: { networkMode: 'PUBLIC' },
    });
    runtime.node.addDependency(role);

    new cdk.CfnOutput(this, 'AgentRuntimeArn', { value: runtime.attrAgentRuntimeArn });
  }
}
