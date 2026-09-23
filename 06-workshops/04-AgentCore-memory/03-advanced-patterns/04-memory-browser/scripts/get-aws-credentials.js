#!/usr/bin/env node

/**
 * Helper script to get temporary AWS credentials for the React app
 * This script uses the AWS CLI configuration to get temporary credentials
 * that can be used in the browser environment.
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

async function getTemporaryCredentials() {
  try {
    console.log('🔑 Setting up AWS configuration for the dashboard...');

    // First, check if we can access AWS
    try {
      const identity = execSync('aws sts get-caller-identity --output json', { encoding: 'utf8' });
      const identityData = JSON.parse(identity);
      console.log(`✅ AWS Identity confirmed: ${identityData.Arn}`);
    } catch (error) {
      throw new Error('AWS CLI not configured or no valid credentials found');
    }

    // Get current AWS region
    let region = null;
    try {
      const configOutput = execSync('aws configure get region', { encoding: 'utf8' });
      region = configOutput.trim();
      if (!region) {
        throw new Error('No AWS region configured. Run: aws configure set region <your-region>');
      }
    } catch (error) {
      throw new Error('AWS region not configured. Run: aws configure set region <your-region>');
    }

    // Create environment variables content for frontend configuration
    const envContent = `
# AgentCore Memory Dashboard - Frontend Configuration
# Generated on: ${new Date().toISOString()}
# 
# Note: This dashboard uses a backend proxy approach for AWS credentials
# since browser applications cannot directly use AWS CLI credentials for security reasons.

# Backend API URL
REACT_APP_BACKEND_URL=http://localhost:8000

# Dashboard Settings
REACT_APP_MAX_MEMORY_ENTRIES=50
REACT_APP_REFRESH_INTERVAL=5000
REACT_APP_DEBUG_MODE=true

# Note: Memory ID, Actor ID, and Session ID are entered by users through the UI
# No hardcoded values needed here
`.trim();

    // Write to .env file
    const envPath = path.join(__dirname, '..', '.env');
    fs.writeFileSync(envPath, envContent);

    console.log('✅ Frontend configuration saved to .env file');
    console.log('🔧 Dashboard configured to use backend proxy for AWS credentials');
    console.log('🚀 You can now run: npm run dev');
    console.log('');
    console.log('📝 Note: Memory ID, Actor ID, and Session ID will be entered through the UI');
    console.log('   No hardcoded values are stored in configuration files.');

  } catch (error) {
    console.error('❌ Error setting up configuration:');
    console.error(error.message);
    console.error('\n💡 Troubleshooting:');
    console.error('1. Run: aws configure list');
    console.error('2. Run: aws sts get-caller-identity');
    console.error('3. Make sure you have AWS CLI installed and configured');
    console.error('4. Ensure your AWS credentials have Bedrock AgentCore permissions');
    process.exit(1);
  }
}

// Run the script
getTemporaryCredentials();