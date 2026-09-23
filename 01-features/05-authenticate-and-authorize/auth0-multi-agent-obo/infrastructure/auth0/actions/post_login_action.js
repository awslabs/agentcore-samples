/**
 * Auth0 Action: Post-Login - Add Custom Claims
 *
 * This action adds custom claims from user metadata to the access token and ID token.
 * Actions are the modern replacement for Rules and offer better performance and features.
 *
 * Custom Claims Added:
 * - customer_id: Unique identifier for the customer in the financial system
 * - account_types: Array of account types the customer has access to
 * - kyc_status: Know Your Customer verification status
 * - security_level: Security clearance level for sensitive operations
 * - preferred_agent: Preferred AI agent for interactions
 *
 * Trigger: Post-Login
 * Runtime: Node.js 18 (Recommended)
 *
 * @param {Event} event - Details about the user and the context
 * @param {PostLoginAPI} api - Interface for accessing Auth0 APIs
 */
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://agentcore.example.com/';

  // Extract custom claims from user metadata
  const userMetadata = event.user.user_metadata || {};
  const appMetadata = event.user.app_metadata || {};

  // Prepare custom claims
  const customClaims = {
    customer_id: userMetadata.customer_id || appMetadata.customer_id || event.user.user_id,
    account_types: userMetadata.account_types || appMetadata.account_types || [],
    kyc_status: userMetadata.kyc_status || appMetadata.kyc_status || 'pending',
    security_level: userMetadata.security_level || appMetadata.security_level || 'basic',
    preferred_agent: userMetadata.preferred_agent || appMetadata.preferred_agent || 'coordinator'
  };

  // Add user roles if present
  if (event.authorization && event.authorization.roles && event.authorization.roles.length > 0) {
    customClaims.roles = event.authorization.roles;
  }

  // Add custom claims to access token
  Object.keys(customClaims).forEach(key => {
    api.accessToken.setCustomClaim(namespace + key, customClaims[key]);
  });

  // Add custom claims to ID token
  Object.keys(customClaims).forEach(key => {
    api.idToken.setCustomClaim(namespace + key, customClaims[key]);
  });

  // Enforce KYC requirements for certain operations
  if (customClaims.kyc_status === 'rejected' || customClaims.kyc_status === 'expired') {
    // Optionally, deny access or reduce scope
    // api.access.deny('KYC verification required');

    // Or just add a warning claim
    api.accessToken.setCustomClaim(namespace + 'kyc_warning', 'KYC verification required for full access');
  }

  // Add enhanced security metadata for premium users
  if (customClaims.security_level === 'premium' || customClaims.security_level === 'enhanced') {
    api.accessToken.setCustomClaim(namespace + 'enhanced_access', true);
  }

  // Add client_id claim (required by AWS Bedrock AgentCore per identity-idp-okta documentation)
  // This must be a top-level claim, not namespaced
  // See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idp-okta.html
  api.accessToken.setCustomClaim('client_id', event.client.client_id);

  // Log for debugging (use sparingly in production)
  console.log('Custom claims added for user:', event.user.user_id);
  console.log('Customer ID:', customClaims.customer_id);
  console.log('KYC Status:', customClaims.kyc_status);
};

/**
 * Optional: Post-Login Continue Handler
 *
 * This handler is called after the post-login action completes.
 * Use it for post-processing or additional validation.
 *
 * @param {Event} event - Details about the user and the context
 * @param {PostLoginAPI} api - Interface for accessing Auth0 APIs
 */
exports.onContinuePostLogin = async (event, api) => {
  // Optional: Add any post-processing logic here
  // This is useful for multi-step flows or deferred operations
};
