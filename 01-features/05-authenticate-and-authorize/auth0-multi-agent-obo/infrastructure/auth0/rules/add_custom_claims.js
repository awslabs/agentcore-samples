/**
 * Auth0 Rule: Add Custom Claims to Tokens
 *
 * This rule adds custom claims from user metadata to the access token and ID token.
 * Claims are namespaced to avoid conflicts with standard OIDC claims.
 *
 * NOTE: Rules are the legacy approach. Consider migrating to Actions for new implementations.
 * See actions/post_login_action.js for the modern Actions-based approach.
 *
 * Custom Claims Added:
 * - customer_id: Unique identifier for the customer in the financial system
 * - account_types: Array of account types the customer has access to
 * - kyc_status: Know Your Customer verification status
 * - security_level: Security clearance level for sensitive operations
 * - preferred_agent: Preferred AI agent for interactions
 *
 * @param {Object} user - The user object
 * @param {Object} context - The authentication context
 * @param {Function} callback - The callback function
 */
function addCustomClaims(user, context, callback) {
  const namespace = 'https://agentcore.example.com/';

  // Extract custom claims from user metadata
  const userMetadata = user.user_metadata || {};
  const appMetadata = user.app_metadata || {};

  // Add custom claims to access token and ID token
  context.accessToken[namespace + 'customer_id'] = userMetadata.customer_id || appMetadata.customer_id || user.user_id;
  context.accessToken[namespace + 'account_types'] = userMetadata.account_types || appMetadata.account_types || [];
  context.accessToken[namespace + 'kyc_status'] = userMetadata.kyc_status || appMetadata.kyc_status || 'pending';
  context.accessToken[namespace + 'security_level'] = userMetadata.security_level || appMetadata.security_level || 'basic';
  context.accessToken[namespace + 'preferred_agent'] = userMetadata.preferred_agent || appMetadata.preferred_agent || 'coordinator';

  // Add user roles if present
  if (user.roles && user.roles.length > 0) {
    context.accessToken[namespace + 'roles'] = user.roles;
  }

  // Add to ID token as well for client-side access
  context.idToken[namespace + 'customer_id'] = context.accessToken[namespace + 'customer_id'];
  context.idToken[namespace + 'account_types'] = context.accessToken[namespace + 'account_types'];
  context.idToken[namespace + 'kyc_status'] = context.accessToken[namespace + 'kyc_status'];
  context.idToken[namespace + 'security_level'] = context.accessToken[namespace + 'security_level'];
  context.idToken[namespace + 'preferred_agent'] = context.accessToken[namespace + 'preferred_agent'];

  if (context.accessToken[namespace + 'roles']) {
    context.idToken[namespace + 'roles'] = context.accessToken[namespace + 'roles'];
  }

  // Log for debugging (remove in production)
  console.log('Custom claims added for user:', user.user_id);
  console.log('Customer ID:', context.accessToken[namespace + 'customer_id']);
  console.log('KYC Status:', context.accessToken[namespace + 'kyc_status']);

  callback(null, user, context);
}
