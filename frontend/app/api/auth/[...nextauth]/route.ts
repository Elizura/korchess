import NextAuth from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import type { JWT } from "next-auth/jwt";

type ExtendedToken = JWT & {
  idToken?: string;
  accessToken?: string;
  refreshToken?: string;
  accessTokenExpiresAt?: number;
  authError?: "RefreshAccessTokenError";
};

const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || "";
const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || "";
const IS_PROD = process.env.NODE_ENV === "production";
const IS_PROD_BUILD = process.env.NEXT_PHASE === "phase-production-build";

// Ensure NextAuth has stable runtime config even if env vars are missing in local dev.
const NEXTAUTH_URL =
  process.env.NEXTAUTH_URL ||
  process.env.AUTH_URL ||
  (IS_PROD ? "" : "http://localhost:3005");
const NEXTAUTH_SECRET =
  process.env.NEXTAUTH_SECRET ||
  process.env.AUTH_SECRET ||
  (IS_PROD_BUILD ? "build-time-nextauth-secret-placeholder" : IS_PROD ? "" : "local-dev-nextauth-secret-change-me");

if (NEXTAUTH_URL && !process.env.NEXTAUTH_URL) {
  process.env.NEXTAUTH_URL = NEXTAUTH_URL;
}
if (NEXTAUTH_SECRET && !process.env.NEXTAUTH_SECRET) {
  process.env.NEXTAUTH_SECRET = NEXTAUTH_SECRET;
}

if (IS_PROD && !IS_PROD_BUILD && !process.env.NEXTAUTH_SECRET && !process.env.AUTH_SECRET) {
  throw new Error("NEXTAUTH_SECRET is required in production.");
}

async function refreshGoogleTokens(token: ExtendedToken): Promise<ExtendedToken> {
  if (!token.refreshToken) {
    return { ...token, authError: "RefreshAccessTokenError" };
  }

  try {
    const body = new URLSearchParams({
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      grant_type: "refresh_token",
      refresh_token: token.refreshToken,
    });

    const response = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
    });

    if (!response.ok) {
      throw new Error(`Refresh failed with status ${response.status}`);
    }

    const refreshed = (await response.json()) as {
      access_token?: string;
      expires_in?: number;
      id_token?: string;
      refresh_token?: string;
    };

    const expiresInMs =
      typeof refreshed.expires_in === "number" && refreshed.expires_in > 0
        ? refreshed.expires_in * 1000
        : 60 * 60 * 1000;

    return {
      ...token,
      accessToken: refreshed.access_token ?? token.accessToken,
      accessTokenExpiresAt: Date.now() + expiresInMs - 60_000,
      idToken: refreshed.id_token ?? token.idToken,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      authError: undefined,
    };
  } catch {
    return {
      ...token,
      authError: "RefreshAccessTokenError",
    };
  }
}

const handler = NextAuth({
  secret: NEXTAUTH_SECRET,
  providers: [
    GoogleProvider({
      clientId: GOOGLE_CLIENT_ID,
      clientSecret: GOOGLE_CLIENT_SECRET,
      authorization: {
        params: {
          access_type: "offline",
          prompt: "consent",
          response_type: "code",
        },
      },
    }),
  ],
  session: {
    strategy: "jwt",
  },
  callbacks: {
    async jwt({ token, account }) {
      const nextToken = token as ExtendedToken;

      if (account) {
        const accountExpiresAtMs =
          typeof account.expires_at === "number"
            ? account.expires_at * 1000
            : Date.now() + 55 * 60 * 1000;

        return {
          ...nextToken,
          idToken: account.id_token ?? nextToken.idToken,
          accessToken: account.access_token ?? nextToken.accessToken,
          refreshToken: account.refresh_token ?? nextToken.refreshToken,
          accessTokenExpiresAt: accountExpiresAtMs,
          authError: undefined,
        };
      }

      if (
        typeof nextToken.accessTokenExpiresAt === "number" &&
        Date.now() < nextToken.accessTokenExpiresAt
      ) {
        return nextToken;
      }

      return refreshGoogleTokens(nextToken);
    },
    async session({ session, token }) {
      const jwtToken = token as ExtendedToken;
      if (jwtToken?.idToken) {
        (session as any).idToken = jwtToken.idToken;
      }
      if (jwtToken?.accessToken) {
        (session as any).accessToken = jwtToken.accessToken;
      }
      if (jwtToken?.refreshToken) {
        (session as any).refreshToken = jwtToken.refreshToken;
      }
      if (typeof jwtToken?.accessTokenExpiresAt === "number") {
        (session as any).accessTokenExpiresAt = jwtToken.accessTokenExpiresAt;
      }
      if (jwtToken?.sub) {
        (session as any).userId = jwtToken.sub;
      }
      if (jwtToken?.authError) {
        (session as any).authError = jwtToken.authError;
      }
      return session;
    },
  },
});

export { handler as GET, handler as POST };
