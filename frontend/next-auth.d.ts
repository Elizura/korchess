import "next-auth";

declare module "next-auth" {
  interface Session {
    idToken?: string;
    userId?: string;
    accessToken?: string;
    refreshToken?: string;
    accessTokenExpiresAt?: number;
    authError?: "RefreshAccessTokenError";
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    idToken?: string;
    accessToken?: string;
    refreshToken?: string;
    accessTokenExpiresAt?: number;
    authError?: "RefreshAccessTokenError";
  }
}
