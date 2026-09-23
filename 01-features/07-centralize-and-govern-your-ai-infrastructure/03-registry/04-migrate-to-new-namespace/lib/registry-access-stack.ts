import { Annotations, CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';

export interface RegistryAccessStackProps extends StackProps {
  readonly accessRoleName: string;
  readonly engineAccountId: string;
  readonly engineRoleArn: string;
  readonly externalId: string;
  readonly previewReadActions: string[];
  readonly previewRegistryResources: string[];
  readonly targetWriteActions: string[];
  readonly targetRegistryResources: string[];
}

export class RegistryAccessStack extends Stack {
  public readonly accessRole: iam.Role;

  public constructor(scope: Construct, id: string, props: RegistryAccessStackProps) {
    super(scope, id, props);

    const trustedEngineRole = new iam.AccountPrincipal(props.engineAccountId).withConditions({
      ArnEquals: {
        'aws:PrincipalArn': props.engineRoleArn,
      },
      StringEquals: {
        'sts:ExternalId': props.externalId,
      },
    });

    this.accessRole = new iam.Role(this, 'RegistryMigrationAccessRole', {
      roleName: props.accessRoleName,
      assumedBy: trustedEngineRole,
      description: 'Scoped source-read and target-write access for the Agent Registry migration engine',
    });

    if (props.previewRegistryResources.length > 0) {
      if (props.previewReadActions.length > 0) {
        this.accessRole.addToPolicy(
          new iam.PolicyStatement({
            sid: 'ReadPreviewRegistries',
            actions: props.previewReadActions,
            resources: props.previewRegistryResources,
          }),
        );
      } else {
        Annotations.of(this).addWarning(
          'No preview Registry IAM actions were configured; add the finalized preview actions before running extraction.',
        );
      }
    }

    if (props.targetRegistryResources.length > 0) {
      if (props.targetWriteActions.length > 0) {
        this.accessRole.addToPolicy(
          new iam.PolicyStatement({
            sid: 'WriteTargetRegistries',
            actions: props.targetWriteActions,
            resources: props.targetRegistryResources,
          }),
        );
      } else {
        Annotations.of(this).addWarning(
          'No target Registry IAM actions were configured; add the finalized actions before disabling dry-run loading.',
        );
      }
    }

    const recordWildcardFindings = [
      ...props.previewRegistryResources,
      ...props.targetRegistryResources,
    ]
      .filter(
        (resource) =>
          resource.endsWith('/record/*') && !resource.slice(0, -1).includes('*'),
      )
      .map((resource) => `Resource::${resource}`);
    if (recordWildcardFindings.length > 0) {
      NagSuppressions.addResourceSuppressionsByPath(
        this,
        `/${this.accessRole.node.path}/DefaultPolicy/Resource`,
        [
          {
            id: 'AwsSolutions-IAM5',
            reason:
              'Cross-account access is restricted to explicit configured registry ARNs. The final ' +
              'record-resource wildcard is required to address arbitrary record children within ' +
              'only those registries; broader configured wildcards remain reportable.',
            appliesTo: recordWildcardFindings,
          },
        ],
      );
    }

    new CfnOutput(this, 'RegistryMigrationAccessRoleArn', {
      value: this.accessRole.roleArn,
    });
  }
}
