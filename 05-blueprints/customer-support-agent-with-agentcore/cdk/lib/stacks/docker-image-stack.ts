import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs/lib/construct';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets'
import { BaseStackProps } from '../types';
import * as path from 'path';

export interface DockerImageStackProps extends BaseStackProps {}

export class DockerImageStack extends cdk.Stack {
    readonly imageUri: string

    constructor(scope: Construct, id: string, props: DockerImageStackProps) {
        super(scope, id, props);

        const asset = new ecr_assets.DockerImageAsset(this, `${props.appName}-AppImage`, {
            platform: ecr_assets.Platform.LINUX_ARM64,
            directory: path.join(__dirname, "../../../"), // path to root of the project
        });

        this.imageUri = asset.imageUri;
        new cdk.CfnOutput(this, 'ImageUri', { value: this.imageUri });
    }
}