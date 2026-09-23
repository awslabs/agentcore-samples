import { Amplify } from 'aws-amplify'
import { Authenticator } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import amplifyConfig from './amplifyconfiguration'
import { ChatProvider } from './hooks/useChat'
import { ChatPage } from './components/ChatPage'

// Configure Amplify
Amplify.configure(amplifyConfig)

function App() {
  // Get stack name from URL query parameter or environment variable
  const params = new URLSearchParams(window.location.search)
  const stackName = params.get('stack') || import.meta.env.VITE_STACK_NAME || 'customer-support-vpc-dev'

  return (
    <Authenticator hideSignUp={true}>
      {({ signOut, user }) => (
        <ChatProvider stackName={stackName}>
          <ChatPage signOut={signOut} user={user} />
        </ChatProvider>
      )}
    </Authenticator>
  )
}

export default App
