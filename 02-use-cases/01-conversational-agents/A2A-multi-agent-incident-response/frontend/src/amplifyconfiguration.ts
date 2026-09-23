import { ResourcesConfig } from 'aws-amplify'

// Remove https:// from Cognito domain if present
const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN?.replace('https://', '').replace('http://', '') || ''

const amplifyConfig: ResourcesConfig = {
  Auth: {
    Cognito: {
      userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID,
      userPoolClientId: import.meta.env.VITE_COGNITO_USER_POOL_CLIENT_ID,
      loginWith: {
        oauth: {
          domain: cognitoDomain,
          scopes: ['email', 'openid', 'profile'],
          redirectSignIn: [window.location.origin],
          redirectSignOut: [window.location.origin],
          responseType: 'code',
        },
      },
    },
  },
}

export default amplifyConfig
