import { Amplify } from 'aws-amplify'
import { Authenticator } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import amplifyConfig from './amplifyconfiguration'
import { ChatProvider } from './hooks/useChat'
import { ChatPage } from './components/ChatPage'

// Configure Amplify
Amplify.configure(amplifyConfig)

function App() {
  return (
    <Authenticator hideSignUp={true}>
      {({ signOut, user }) => (
        <ChatProvider>
          <ChatPage signOut={signOut} user={user} />
        </ChatProvider>
      )}
    </Authenticator>
  )
}

export default App
