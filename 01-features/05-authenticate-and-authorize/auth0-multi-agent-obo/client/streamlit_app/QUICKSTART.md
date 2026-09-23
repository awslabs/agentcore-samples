# Quick Start Guide

Get up and running with the AgentCore Identity Streamlit client in 5 minutes.

## Step 1: Install Dependencies

```bash
cd client/streamlit_app
pip install -r requirements.txt
```

## Step 2: Configure Environment

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your Auth0 and AWS AgentCore settings:

```bash
# Minimum required configuration
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_CLIENT_ID=your-client-id
AUTH0_CLIENT_SECRET=your-client-secret
COORDINATOR_AGENT_ID=your-coordinator-agent-id
```

## Step 3: Configure Auth0

1. Go to [Auth0 Dashboard](https://manage.auth0.com/)
2. Create a new Application (type: Single Page Application)
3. Configure settings:
   - **Allowed Callback URLs**: `http://localhost:9090/callback`
   - **Allowed Logout URLs**: `http://localhost:8501`
   - **Allowed Web Origins**: `http://localhost:8501`
   - **Grant Types**: Enable "Authorization Code" and "Refresh Token"

4. Create an API in Auth0:
   - **Identifier**: `https://agentcore-financial-api`
   - Enable "Allow Offline Access" for refresh tokens

5. Copy your Domain, Client ID, and Client Secret to `.env`

## Step 4: Configure AWS Credentials

Ensure AWS credentials are configured:

```bash
aws configure
```

Or set environment variables:

```bash
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
export AWS_REGION=us-east-1
```

## Step 5: Run the Application

Using the launch script:

```bash
./run.sh
```

Or directly with Streamlit:

```bash
streamlit run app.py
```

## Step 6: Test Authentication

1. Open browser to `http://localhost:8501`
2. Click "Sign in with Auth0"
3. Complete authentication in browser
4. Return to application - you should see the chat interface

## Step 7: Test Chat

Try these sample queries:

```
Show me my customer profile
What accounts do I have?
Show my recent transactions
```

## Troubleshooting

### Can't connect to Auth0

- Verify `AUTH0_DOMAIN` in `.env`
- Check Auth0 application is not disabled
- Ensure callback URL matches exactly

### Token validation fails

- Verify `AUTH0_AUDIENCE` matches your Auth0 API identifier
- Check JWT authorizer in AgentCore is configured correctly
- Ensure custom claims are being added by Auth0 Action

### Agent invocation fails

- Verify `COORDINATOR_AGENT_ID` is correct
- Check AWS credentials have `bedrock:InvokeAgent` permission
- Ensure agent is deployed and active

### Port conflicts

Change ports in `.env`:

```bash
STREAMLIT_PORT=8502
OAUTH_CALLBACK_PORT=9091
```

## Next Steps

- Review [README.md](README.md) for detailed documentation
- Explore agent configuration in `shared/config/settings.py`
- Customize UI components in `components/` directory
- Add custom claims in Auth0 Actions
- Configure agent tools with authorization rules

## Support

For issues or questions:

1. Check application logs in terminal
2. Enable debug mode: `export DEBUG=true`
3. Review Auth0 logs in dashboard
4. Check CloudWatch logs for agent invocations
