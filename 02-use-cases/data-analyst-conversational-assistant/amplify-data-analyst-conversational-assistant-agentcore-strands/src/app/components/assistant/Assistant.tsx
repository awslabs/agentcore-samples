'use client';

import type { AssistantConfig } from './types';
import Chat from './ui/Chat';

interface AssistantProps {
  config: AssistantConfig;
  /** Cognito Identity Pool ID from amplify_outputs.json */
  identityPoolId: string;
  /** Cognito User Pool ID from amplify_outputs.json */
  userPoolId: string;
}

/**
 * Self-contained AI Assistant component.
 *
 * Drop this into any page that has Amplify configured and authenticated.
 * It renders the chat UI: messages area and input.
 *
 * The page that hosts this component is responsible for the header
 * (app name, user info, sign-out) and footer (copyright, branding).
 */
export default function Assistant({ config, identityPoolId, userPoolId }: AssistantProps) {
  return (
    <Chat config={config} identityPoolId={identityPoolId} userPoolId={userPoolId} />
  );
}
