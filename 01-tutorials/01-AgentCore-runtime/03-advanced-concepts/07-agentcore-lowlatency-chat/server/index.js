import express from "express";
import cors from "cors";
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import yaml from "js-yaml";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const rootDir = join(__dirname, "..");

const app = express();
app.use(cors());
app.use(express.json());

// Read configuration files
const agentcoreConfig = yaml.load(
  readFileSync(join(rootDir, ".bedrock_agentcore.yaml"), "utf8"),
);
const cognitoConfig = JSON.parse(
  readFileSync(join(rootDir, ".agentcore_identity_cognito_user.json"), "utf8"),
);

// Expose configuration to frontend
app.get("/config.json", (req, res) => {
  const config = {
    agentId: agentcoreConfig.agents.chat_bot_agent.bedrock_agentcore.agent_id,
    agentArn: agentcoreConfig.agents.chat_bot_agent.bedrock_agentcore.agent_arn,
    region: agentcoreConfig.agents.chat_bot_agent.aws.region,
    cognito: {
      poolId: cognitoConfig.runtime.pool_id,
      clientId: cognitoConfig.runtime.client_id,
      discoveryUrl: cognitoConfig.runtime.discovery_url,
      domainPrefix: cognitoConfig.runtime.domain_prefix,
      username: cognitoConfig.runtime.username,
      password: cognitoConfig.runtime.password,
    },
  };
  res.json(config);
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});
