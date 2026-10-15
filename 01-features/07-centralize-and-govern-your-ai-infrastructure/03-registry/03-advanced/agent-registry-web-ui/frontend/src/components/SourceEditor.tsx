// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Tiles from "@cloudscape-design/components/tiles";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import Link from "@cloudscape-design/components/link";
import Button from "@cloudscape-design/components/button";
import type { RecordType } from "../api/records";
import { config } from "../config";
import {
  supportsSource,
  type CredentialMode,
  type SourceConfig,
} from "./source";

const DOC_SYNC =
  "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-sync-records.html";

interface Props {
  recordType: RecordType;
  value: SourceConfig;
  onChange: (next: SourceConfig) => void;
}

/**
 * "Source & authentication" — where the descriptor content comes from, and how the
 * registry authenticates when it goes and fetches it.
 */
export default function SourceEditor({ recordType, value, onChange }: Props) {
  const set = <K extends keyof SourceConfig>(key: K, v: SourceConfig[K]) =>
    onChange({ ...value, [key]: v });

  const syncable = supportsSource(recordType);

  return (
    <SpaceBetween size="l">
      {!syncable && (
        <Alert
          type="info"
          header={`${recordType} records are always authored inline`}
        >
          Only the <Box variant="code">mcpServer</Box> and{" "}
          <Box variant="code">a2aAgentCard</Box> descriptors have a{" "}
          <Box variant="code">source</Box> field, so URL synchronization is not
          available for {recordType}. Provide the descriptor JSON on the
          previous step.
        </Alert>
      )}

      <FormField
        label="Descriptor source"
        description="Where the registry gets this record's descriptor content."
      >
        <Tiles
          value={value.mode}
          onChange={({ detail }) =>
            set("mode", detail.value as SourceConfig["mode"])
          }
          items={[
            {
              value: "inline",
              label: "Inline descriptor",
              description:
                "You author the descriptor JSON yourself (the Descriptor step). Nothing is fetched.",
            },
            {
              value: "fromUrl",
              label: "Synchronize from endpoint",
              description:
                "The registry connects to a live endpoint, introspects its server and tool definitions, and fills the descriptor in for you.",
              disabled: !syncable,
            },
          ]}
        />
      </FormField>

      {value.mode === "fromUrl" && (
        <SpaceBetween size="l">
          <Alert
            type="info"
            header="This URL is the live endpoint, not a document"
          >
            The registry <b>invokes</b> this URL and reads the server and tool
            definitions from it — it is not a link to a static{" "}
            <Box variant="code">server.json</Box>. For an AgentCore Gateway or
            Runtime, use its MCP endpoint. The descriptor you typed on the
            previous step is ignored, and the registry may also overwrite the
            record's name, description and version with what it finds.{" "}
            <Link external href={DOC_SYNC}>
              Synchronize records from external sources
            </Link>
          </Alert>

          <FormField
            label="Source URL"
            description="The endpoint the registry will call. Must be https."
            secondaryControl={
              config.sampleGatewayUrl ? (
                <Button onClick={() => set("url", config.sampleGatewayUrl)}>
                  Use sample gateway
                </Button>
              ) : undefined
            }
          >
            <Input
              value={value.url}
              placeholder="https://my-gateway-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
              onChange={({ detail }) => set("url", detail.value)}
            />
          </FormField>

          <FormField
            label="Outbound authentication"
            description="The credential the registry presents when it calls the source URL."
          >
            <RadioGroup
              value={value.credentialMode}
              onChange={({ detail }) =>
                set("credentialMode", detail.value as CredentialMode)
              }
              items={[
                {
                  value: "OAUTH",
                  label: "OAuth 2.0 via AgentCore Identity (recommended)",
                  description:
                    "The registry fetches a machine-to-machine token from an AgentCore Identity credential provider. This sample's provider is wired to the same Cognito user pool you signed in with, so it matches a Gateway using CUSTOM_JWT.",
                },
                {
                  value: "IAM",
                  label: "IAM SigV4",
                  description:
                    "The registry assumes a role and SigV4-signs the request. Use for a Gateway or Runtime whose inbound auth is AWS_IAM.",
                },
                {
                  value: "NONE",
                  label: "None",
                  description:
                    "The source is a public, unauthenticated endpoint.",
                },
              ]}
            />
          </FormField>

          {value.credentialMode === "OAUTH" && (
            <SpaceBetween size="m">
              {!config.oauthCredentialProviderArn && (
                <Alert
                  type="warning"
                  header="No credential provider is configured"
                >
                  This deployment has no{" "}
                  <Box variant="code">VITE_OAUTH_CREDENTIAL_PROVIDER_ARN</Box>.
                  Run{" "}
                  <Box variant="code">
                    deploy/setup/06-sample-mcp-gateway.sh up
                  </Box>{" "}
                  to create an AgentCore Identity provider against this sample's
                  Cognito pool, or paste an existing provider ARN below.
                </Alert>
              )}
              <FormField
                label="Credential provider ARN"
                description="An OAuth 2.0 credential provider in Amazon Bedrock AgentCore Identity. Must live in the same account as the registry."
                constraintText="arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/oauth2credentialprovider/<name>"
              >
                <Input
                  value={value.oauthProviderArn}
                  onChange={({ detail }) =>
                    set("oauthProviderArn", detail.value)
                  }
                />
              </FormField>
              <FormField
                label="Scopes"
                description="optional — space or comma separated. Requested with the client_credentials grant."
              >
                <Input
                  value={value.oauthScopes}
                  placeholder="sample-mcp/invoke"
                  onChange={({ detail }) => set("oauthScopes", detail.value)}
                />
              </FormField>
              <Box color="text-body-secondary" fontSize="body-s">
                Publishing a record with an OAuth source additionally requires{" "}
                <Box variant="code">
                  bedrock-agentcore:GetWorkloadAccessToken
                </Box>{" "}
                and{" "}
                <Box variant="code">
                  bedrock-agentcore:GetResourceOauth2Token
                </Box>{" "}
                on your role — both are granted to the Publisher persona in this
                sample.
              </Box>
            </SpaceBetween>
          )}

          {value.credentialMode === "IAM" && (
            <SpaceBetween size="m">
              <FormField
                label="Role ARN"
                description="The role the registry assumes to sign the request. It needs permission to invoke the source (e.g. bedrock-agentcore:InvokeGateway)."
              >
                <Input
                  value={value.iamRoleArn}
                  placeholder="arn:aws:iam::111122223333:role/registry-sync-role"
                  onChange={({ detail }) => set("iamRoleArn", detail.value)}
                />
              </FormField>
              <FormField
                label="Signing service"
                description="bedrock-agentcore for an AgentCore Gateway or Runtime; execute-api for API Gateway; lambda for a function URL."
              >
                <Input
                  value={value.iamService}
                  onChange={({ detail }) => set("iamService", detail.value)}
                />
              </FormField>
              <FormField
                label="Signing region"
                description="optional — defaults to the registry's region."
              >
                <Input
                  value={value.iamRegion}
                  onChange={({ detail }) => set("iamRegion", detail.value)}
                />
              </FormField>
              <Box color="text-body-secondary" fontSize="body-s">
                The SigV4 path also needs <Box variant="code">iam:PassRole</Box>{" "}
                on that role with{" "}
                <Box variant="code">
                  iam:PassedToService = agent-registry.amazonaws.com
                </Box>
                , and the role must trust{" "}
                <Box variant="code">agent-registry.amazonaws.com</Box>.
              </Box>
            </SpaceBetween>
          )}
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
}
