import NextAuth from "next-auth"
import "next-auth/jwt"

import Cognito from "next-auth/providers/cognito"

export const { handlers, auth, signIn, signOut } = NextAuth({
  debug: !!process.env.AUTH_DEBUG,
  theme: { logo: "https://authjs.dev/img/logo-sm.png" },
  providers: [
    Cognito
  ],
  basePath: "/auth",
  session: { strategy: "jwt" },
  callbacks: {
    authorized({ request, auth }) {
      const { pathname } = request.nextUrl
      if (pathname === "/middleware-example") return !!auth
      return true
    },
    jwt({ token, trigger, session, account }) {
      if (trigger === "update") token.name = session.user.name
      if (account?.provider === "keycloak") {
        return { ...token, accessToken: account.access_token }
      }
      if (account?.provider === "github") {
        return { ...token, accessToken: account.access_token }
      }
      if (account?.provider === "cognito") {
        return { ...token, accessToken: account.access_token }
      }
      return token
    },
    async session({ session, token }) {
      console.log("Session status:", session ? "authenticated" : "unauthenticated");
      console.log("Session data:", session);
      
      if (token?.accessToken) session.accessToken = token.accessToken

      return session
    },
  },
  experimental: { enableWebAuthn: true },
})

declare module "next-auth" {
  interface Session {
    accessToken?: string
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string
  }
}
