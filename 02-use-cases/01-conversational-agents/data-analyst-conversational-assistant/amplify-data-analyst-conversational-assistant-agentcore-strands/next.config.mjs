/** @type {import('next').NextConfig} */
const nextConfig = {

  // Pass server-side env vars to Next.js runtime (required for Amplify Hosting SSR)
  env: {
    APP_NAME: process.env.APP_NAME,
    APP_DESCRIPTION: process.env.APP_DESCRIPTION,
    AGENT_RUNTIME_ARN: process.env.AGENT_RUNTIME_ARN,
    AGENT_ENDPOINT_NAME: process.env.AGENT_ENDPOINT_NAME,
    WELCOME_MESSAGE: process.env.WELCOME_MESSAGE,
    MAX_LENGTH_INPUT_SEARCH: process.env.MAX_LENGTH_INPUT_SEARCH,
    MODEL_ID_FOR_CHART: process.env.MODEL_ID_FOR_CHART,
    QUESTION_ANSWERS_TABLE_NAME: process.env.QUESTION_ANSWERS_TABLE_NAME,
    MEMORY_ID: process.env.MEMORY_ID,
  },

  // Required for AWS Amplify UI React components
  transpilePackages: ['@aws-amplify/ui-react', '@aws-amplify/ui'],
};


export default nextConfig;
