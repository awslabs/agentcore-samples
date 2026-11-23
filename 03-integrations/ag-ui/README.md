# AG-UI
This sample demonstrates AG-UI/Strands/AgentCore integration.

## Prerequisites
You need to have access to an AWS account and you need the following tools installed locally:
1. AWS CLI
2. AgentCore CLI
3. Node.js, npm, and npx

## Steps
1. Set your working directory and the AWS region you will use:
```sh
export WORK_DIR=~
export AWS_REGION=us-west-2
```

2. Clone the source code:
```sh
cd ${WORK_DIR}
git clone https://github.com/awslabs/amazon-bedrock-agentcore-samples.git
cd amazon-bedrock-agentcore-samples/03-integrations/ag-ui
```

3. Create a Cognito user pool - it will be used for authenticating the agent users:
```sh
export USER_POOL_ID=$(aws cognito-idp create-user-pool \
  --pool-name "AgentCoreAgUiPool" \
  --policies '{"PasswordPolicy":{"MinimumLength":8}}' \
  --region ${AWS_REGION} \
  --query 'UserPool.Id' --output text)
export DISCOVERY_URL="https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"
```

4. Create a Cognito user pool app client for the agent web app:
```sh
export CLIENT_ID=$(aws cognito-idp create-user-pool-client \
  --user-pool-id ${USER_POOL_ID} \
  --client-name "AgentCoreAgUiPoolClient" \
  --generate-secret \
  --supported-identity-providers "COGNITO" \
  --callback-urls "http://localhost:3000/auth/callback/cognito" \
  --allowed-o-auth-flows "code" \
  --allowed-o-auth-scopes "openid" "email" "profile" "phone" "aws.cognito.signin.user.admin" \
  --allowed-o-auth-flows-user-pool-client \
  --region ${AWS_REGION} \
  --query 'UserPoolClient.ClientId' --output text)
```

3. Retrieve the Cognito user pool app client secret - it will be added to the web app config:
```sh
export CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id ${USER_POOL_ID} \
  --client-id ${CLIENT_ID} \
  --region ${AWS_REGION} \
  --query 'UserPoolClient.ClientSecret' --output text)
```

4. Create a domain for the Cognito user pool:
```sh
aws cognito-idp create-user-pool-domain \
  --domain "ag-ui-pool-${RANDOM}" \
  --user-pool-id ${USER_POOL_ID} \
  --region ${AWS_REGION}
```

5. Create a test user:
```sh
aws cognito-idp admin-create-user \
  --user-pool-id ${USER_POOL_ID} \
  --username "testuser" \
  --temporary-password "Temp1234" \
  --message-action "SUPPRESS" \
  --region ${AWS_REGION}
```

6. Set the test user password:
```sh
aws cognito-idp admin-set-user-password \
  --user-pool-id ${USER_POOL_ID} \
  --username "testuser" \
  --password "MyPassword123" \
  --permanent \
  --region ${AWS_REGION}
```

7. Create AgentCore config for the agent:
```sh
cd ${WORK_DIR}/amazon-bedrock-agentcore-samples/03-integrations/ag-ui/agent
agentcore configure -e main.py \
  --name ag_ui_agent \
  --requirements-file pyproject.toml \
  --deployment-type container \
  --authorizer-config "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"$DISCOVERY_URL\",\"allowedClients\":[\"$CLIENT_ID\"]}}" \
  --disable-memory \
  --non-interactive
```

8. Deploy the agent into AgentCore:
```sh
agentcore deploy
```

9. Extract the agent ARN manually and set in an env var:
```sh
agentcore status --agent ag_ui_agent
```

```sh
export AGENT_ARN=arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/ag_ui_agent-VBbnrgBedH
export AGENT_ARN_ESCAPED=$(echo "$AGENT_ARN" | sed 's/:/%3A/g; s/\//%2F/g')
```

10. Create the env file for the agent web app:
```sh
cd ${WORK_DIR}/amazon-bedrock-agentcore-samples/03-integrations/ag-ui
envsubst < .env.example > .env.local
```

11. Generate a random string and add it to the .env file - it is required by the AuthN library:
```sh
cd ${WORK_DIR}/amazon-bedrock-agentcore-samples/03-integrations/ag-ui
npx auth secret
```

12. Install libraries dependencies and run the agent web app:
```sh
cd ${WORK_DIR}/amazon-bedrock-agentcore-samples/03-integrations/ag-ui
npm install
npm run dev:ui
```

13. Open the agent web app at http://localhost:3000 and test integration with the agent running on AgentCore. AG-UI protocol is used for the interation between the web app and the agent.

## Contributing

Feel free to submit issues and enhancement requests! This starter is designed to be easily extensible.

## License

This project is licensed under the MIT License - see the LICENSE file for details.