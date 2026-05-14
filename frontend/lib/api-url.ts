const envApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

if (!envApiBaseUrl) {
  throw new Error(
    "NEXT_PUBLIC_API_BASE_URL environment variable is required. " +
    "Set it in your .env file or pass it as a Docker build arg."
  );
}

export const API_BASE_URL = envApiBaseUrl;
